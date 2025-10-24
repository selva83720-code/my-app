#!/bin/bash
# Check if Flask app is responding
curl -f http://localhost:5004/ || exit 1
