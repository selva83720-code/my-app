#!/bin/bash
cd /home/ec2-user/my-app
python3 -m venv venv || true
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

