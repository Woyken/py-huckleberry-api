#!/usr/bin/env node
import { promises as fs } from "node:fs";
import path from "node:path";
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import * as prettier from "prettier";
import deobfuscatorPkg from "obfuscator-io-deobfuscator";

const { deobfuscate } = deobfuscatorPkg;
const DEOBF_TIMEOUT_MS = 60_000;

function printUsage() {
  console.log(`Usage:
  node scripts/deobf-pretty.mjs <directory> [--parallel <n>] [--recursive]

Options:
  --parallel <n>   Number of files to process concurrently (default: 8)
  --recursive      Walk directory recursively
`);
}

function parseArgs(argv) {
  const args = [...argv];
  const options = {
    directory: "",
    parallel: 8,
    recursive: false,
  };

  if (args.length === 0 || args.includes("-h") || args.includes("--help")) {
    printUsage();
    process.exit(0);
  }

  options.directory = args.shift() ?? "";

  while (args.length > 0) {
    const arg = args.shift();
    if (arg === "--parallel") {
      const value = Number.parseInt(args.shift() ?? "", 10);
      if (Number.isNaN(value) || value < 1) {
        throw new Error("--parallel must be a positive integer");
      }
      options.parallel = value;
      continue;
    }

    if (arg === "--recursive") {
      options.recursive = true;
      continue;
    }

    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

async function listInputFiles(rootDir, recursive) {
  const files = [];

  async function walk(currentDir) {
    const entries = await fs.readdir(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        if (recursive) {
          await walk(fullPath);
        }
        continue;
      }

      if (!entry.isFile()) {
        continue;
      }

      if (!entry.name.endsWith(".js")) {
        continue;
      }

      if (entry.name.endsWith(".deobf.js") || entry.name.endsWith(".deobf.pretty.js")) {
        continue;
      }

      files.push(fullPath);
    }
  }

  await walk(rootDir);
  files.sort((a, b) => a.localeCompare(b));
  return files;
}

async function processOne(filePath, index, total, rootDir) {
  const dir = path.dirname(filePath);
  const base = path.basename(filePath, ".js");
  const deobfPath = path.join(dir, `${base}.deobf.js`);
  const prettyPath = path.join(dir, `${base}.deobf.pretty.js`);

  const result = {
    file: path.relative(rootDir, filePath),
    deobf: "",
    pretty: "",
  };

  try {
    await fs.access(deobfPath);
    result.deobf = "skip_exists";
  } catch {
    try {
      const source = await fs.readFile(filePath, "utf8");
      const output = deobfuscate(source);
      await fs.writeFile(deobfPath, output, "utf8");
      result.deobf = "ok";
    } catch {
      result.deobf = "fail";
      result.pretty = "not_run";
      console.log(`[${index}/${total}] FAIL_DEOBF ${result.file}`);
      return result;
    }
  }

  try {
    await fs.access(prettyPath);
    result.pretty = "skip_exists";
    console.log(`[${index}/${total}] ${result.deobf === "ok" ? "OK" : "SKIP"}_DEOBF ${result.file}`);
    console.log(`[${index}/${total}] SKIP_PRETTY_EXISTS ${result.file}`);
    return result;
  } catch {
  }

  if (result.deobf === "ok") {
    console.log(`[${index}/${total}] OK_DEOBF ${result.file}`);
  } else {
    console.log(`[${index}/${total}] SKIP_DEOBF_EXISTS ${result.file}`);
  }

  try {
    const deobfSource = await fs.readFile(deobfPath, "utf8");
    const prettySource = await prettier.format(deobfSource, {
      parser: "babel",
      filepath: prettyPath,
    });
    await fs.writeFile(prettyPath, prettySource, "utf8");
  } catch {
    result.pretty = "fail";
    console.log(`[${index}/${total}] FAIL_PRETTY ${result.file}`);
    return result;
  }

  result.pretty = "ok";
  console.log(`[${index}/${total}] OK_PRETTY ${result.file}`);
  return result;
}

async function fallbackPrettyOnly(filePath, index, total, rootDir) {
  const dir = path.dirname(filePath);
  const base = path.basename(filePath, ".js");
  const prettyPath = path.join(dir, `${base}.deobf.pretty.js`);

  try {
    await fs.access(prettyPath);
    console.log(`[${index}/${total}] TIMEOUT_DEOBF ${path.relative(rootDir, filePath)}`);
    console.log(`[${index}/${total}] SKIP_PRETTY_EXISTS ${path.relative(rootDir, filePath)}`);
    return {
      file: path.relative(rootDir, filePath),
      deobf: "timeout",
      pretty: "skip_exists",
    };
  } catch {
  }

  try {
    const source = await fs.readFile(filePath, "utf8");
    const prettySource = await prettier.format(source, {
      parser: "babel",
      filepath: prettyPath,
    });
    await fs.writeFile(prettyPath, prettySource, "utf8");
    console.log(`[${index}/${total}] TIMEOUT_DEOBF ${path.relative(rootDir, filePath)}`);
    console.log(`[${index}/${total}] OK_PRETTY_FALLBACK ${path.relative(rootDir, filePath)}`);
    return {
      file: path.relative(rootDir, filePath),
      deobf: "timeout",
      pretty: "ok",
    };
  } catch {
    console.log(`[${index}/${total}] TIMEOUT_DEOBF ${path.relative(rootDir, filePath)}`);
    console.log(`[${index}/${total}] FAIL_PRETTY ${path.relative(rootDir, filePath)}`);
    return {
      file: path.relative(rootDir, filePath),
      deobf: "timeout",
      pretty: "fail",
    };
  }
}

async function runWithConcurrency(files, limit, rootDir) {
  const results = new Array(files.length);
  const maxWorkers = Math.min(limit, files.length);
  const running = new Set();
  let nextIndex = 0;

  function launchOne(fileIndex) {
    const task = new Promise((resolve) => {
      let workerResult;
      let timedOut = false;
      const worker = new Worker(new URL(import.meta.url), {
        workerData: {
          filePath: files[fileIndex],
          index: fileIndex + 1,
          total: files.length,
          rootDir,
        },
      });

      const timeoutHandle = setTimeout(() => {
        timedOut = true;
        void worker.terminate();
      }, DEOBF_TIMEOUT_MS);

      worker.on("message", (message) => {
        if (message?.ok && message.result) {
          workerResult = message.result;
        } else {
          workerResult = {
            file: path.relative(rootDir, files[fileIndex]),
            deobf: "fail",
            pretty: "not_run",
          };
        }
      });

      worker.on("error", () => {
        workerResult = {
          file: path.relative(rootDir, files[fileIndex]),
          deobf: "fail",
          pretty: "not_run",
        };
      });

      worker.on("exit", async () => {
        clearTimeout(timeoutHandle);
        if (timedOut) {
          results[fileIndex] = await fallbackPrettyOnly(
            files[fileIndex],
            fileIndex + 1,
            files.length,
            rootDir,
          );
          resolve();
          return;
        }

        results[fileIndex] = workerResult ?? {
          file: path.relative(rootDir, files[fileIndex]),
          deobf: "fail",
          pretty: "not_run",
        };
        resolve();
      });
    });

    running.add(task);
    task.finally(() => running.delete(task));
  }

  while (nextIndex < files.length && running.size < maxWorkers) {
    launchOne(nextIndex);
    nextIndex += 1;
  }

  while (running.size > 0) {
    await Promise.race(running);
    while (nextIndex < files.length && running.size < maxWorkers) {
      launchOne(nextIndex);
      nextIndex += 1;
    }
  }

  return results;
}

function summarize(results) {
  const summary = {
    okDeobf: 0,
    skipDeobf: 0,
    timeoutDeobf: 0,
    failDeobf: 0,
    okPretty: 0,
    skipPretty: 0,
    failPretty: 0,
  };

  for (const r of results) {
    if (r.deobf === "ok") summary.okDeobf += 1;
    if (r.deobf === "skip_exists") summary.skipDeobf += 1;
    if (r.deobf === "timeout") summary.timeoutDeobf += 1;
    if (r.deobf === "fail") summary.failDeobf += 1;

    if (r.pretty === "ok") summary.okPretty += 1;
    if (r.pretty === "skip_exists") summary.skipPretty += 1;
    if (r.pretty === "fail") summary.failPretty += 1;
  }

  return summary;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(String(error.message ?? error));
    printUsage();
    process.exit(1);
  }

  const directory = path.resolve(options.directory);
  const stat = await fs.stat(directory).catch(() => null);
  if (!stat || !stat.isDirectory()) {
    console.error(`Directory does not exist: ${directory}`);
    process.exit(1);
  }

  const files = await listInputFiles(directory, options.recursive);
  if (files.length === 0) {
    console.log("No input .js files found.");
    return;
  }

  console.log(`Starting run: files=${files.length}, parallel=${options.parallel}, recursive=${options.recursive}`);
  const results = await runWithConcurrency(files, options.parallel, directory);
  const summary = summarize(results);

  console.log("\nSummary:");
  console.log(`OK_DEOBF=${summary.okDeobf}`);
  console.log(`SKIP_DEOBF_EXISTS=${summary.skipDeobf}`);
  console.log(`TIMEOUT_DEOBF=${summary.timeoutDeobf}`);
  console.log(`FAIL_DEOBF=${summary.failDeobf}`);
  console.log(`OK_PRETTY=${summary.okPretty}`);
  console.log(`SKIP_PRETTY_EXISTS=${summary.skipPretty}`);
  console.log(`FAIL_PRETTY=${summary.failPretty}`);

  if (summary.failDeobf > 0 || summary.failPretty > 0) {
    process.exitCode = 2;
  }
}

async function runWorkerMode() {
  try {
    const { filePath, index, total, rootDir } = workerData;
    const result = await processOne(filePath, index, total, rootDir);
    parentPort?.postMessage({ ok: true, result });
  } catch {
    parentPort?.postMessage({ ok: false });
  }
}

if (isMainThread) {
  await main();
} else {
  await runWorkerMode();
}
