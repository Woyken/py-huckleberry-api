param([Parameter(Mandatory=$true)][string]$Name)
$ErrorActionPreference = 'Stop'
$mutex = [System.Threading.Mutex]::new($false, $Name)
$owned = $false
try {
    try { $owned = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) {
        [Console]::Out.WriteLine('BUSY')
        [Console]::Out.Flush()
        exit 3
    }
    [Console]::Out.WriteLine('LOCKED')
    [Console]::Out.Flush()
    # Node keeps this pipe open for the command. EOF also arrives after a crash.
    $null = [Console]::In.ReadLine()
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
