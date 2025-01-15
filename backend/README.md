# Live-Agent

Install ffmpeg 
```bash
sudo -i
sudo apt-get update
sudo apt-get install ffmpeg
sudo apt-get install python3 python3-pip python3-dev
sudo apt-get install python3-venv
git clone https://github.com/Reynold97/Live-Agent.git
python3 -m venv /root/Live-Agent/backend/venv
source /root/Live-Agent/backend/venv/bin/activate
pip install -r /root/Live-Agent/backend/requirements.txt
```

```bash
sudo nano /root/Live-Agent/backend/.env
```

```bash
sudo mkdir -p /root/Live-Agent/backend/logs
sudo chown root:root /root/Live-Agent/backend/logs
sudo chmod 755 /root/Live-Agent/backend/logs
sudo touch /root/Live-Agent/backend/logs/agent2.log
sudo chown root:root /root/Live-Agent/backend/logs/agent2.log
sudo chmod 644 /root/Live-Agent/backend/logs/agent2.log
 ```
 
```bash
sudo nano /etc/systemd/system/agent2.service
```

```bash
[Unit]
Description=Agent2 Python Script
After=network.target

[Service]
ExecStart=/root/Live-Agent/backend/venv/bin/python /root/Live-Agent/backend/src/agent2_fr.py start
WorkingDirectory=/root/Live-Agent/backend
Restart=always
User=root
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:/root/Live-Agent/backend/logs/agent2.log
StandardError=append:/root/Live-Agent/backend/logs/agent2.log

[Install]
WantedBy=multi-user.target
 ```
```bash
sudo systemctl daemon-reload
sudo systemctl start agent2.service
sudo systemctl enable agent2.service
sudo systemctl status agent2.service
sudo systemctl stop agent2.service
journalctl -u agent2.service -f

sudo systemctl stop agent2.service
sudo systemctl restart agent2.service
```

https://agents-playground.livekit.io/

```bash
sudo mkdir -p /root/Live-Agent/backend/logs
sudo chown root:root /root/Live-Agent/backend/logs
sudo chmod 755 /root/Live-Agent/backend/logs
sudo touch /root/Live-Agent/backend/logs/conserje.log
sudo chown root:root /root/Live-Agent/backend/logs/conserje.log
sudo chmod 644 /root/Live-Agent/backend/logs/conserje.log
 ```

```bash
[Unit]
Description=Agent2 Python Script
After=network.target

[Service]
ExecStart=/root/Live-Agent/backend/venv/bin/python /root/Live-Agent/backend/src/conserje.py start
WorkingDirectory=/root/Live-Agent/backend
Restart=always
User=root
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:/root/Live-Agent/backend/logs/agent2.log
StandardError=append:/root/Live-Agent/backend/logs/agent2.log

[Install]
WantedBy=multi-user.target
 ```