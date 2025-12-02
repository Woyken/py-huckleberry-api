"""Constants for Huckleberry API."""
from typing import Final

# Firebase configuration
FIREBASE_API_KEY: Final = "AIzaSyApGVHktXeekGyAt-G6dIeWHUkq2oXqcjg"
FIREBASE_PROJECT_ID: Final = "simpleintervals"
FIREBASE_APP_ID: Final = "1:219218185774:android:a3e215cc246b92b0"

# API endpoints
AUTH_URL: Final = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL: Final = "https://securetoken.googleapis.com/v1/token"
FIRESTORE_BASE_URL: Final = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
