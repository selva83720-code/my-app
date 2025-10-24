#!/bin/bash
cd /home/ec2-user/my-app
source venv/bin/activate
pkill -f app.py || true
nohup python app.py > app.log 2>&1 &
