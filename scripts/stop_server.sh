#!/bin/bash
# Stop any Flask app running on port 5004
fuser -k 5004/tcp || true
