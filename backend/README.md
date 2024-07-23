# Live-Agent

Install ffmpeg 
```bash
apt-get update
apt-get install ffmpeg
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
sudo mkdir -p /root/Live-Agent/backend/logs
sudo chown root:root /root/Live-Agent/backend/logs
sudo chmod 755 /root/Live-Agent/backend/logs
sudo touch /root/Live-Agent/backend/logs/agent2.log
sudo chown root:root /root/Live-Agent/backend/logs/agent2.log
sudo chmod 644 /root/Live-Agent/backend/logs/agent2.log
 ```

```bash
sudo nano /etc/systemd/system/agent2.service
sudo systemctl daemon-reload
sudo systemctl start agent2.service
sudo systemctl enable agent2.service
sudo systemctl status agent2.service
sudo systemctl stop agent2.service
journalctl -u agent2.service -f

sudo systemctl stop agent2.service
sudo systemctl restart agent2.service
```