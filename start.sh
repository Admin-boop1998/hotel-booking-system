#!/bin/bash
cd "$(dirname "$0")"
echo "════════════════════════════════════════════"
echo "  Quota Solutions — Travel Desk v9.3"
echo "════════════════════════════════════════════"
echo ""
echo "Starting on http://localhost:8100"
echo ""
cd backend && uvicorn main:app --host 0.0.0.0 --port 8100 --reload
