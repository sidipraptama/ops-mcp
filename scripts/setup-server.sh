#!/bin/bash
# Run once on EC2 after first deploy.

set -e

BOT_DIR="/home/ubuntu/ops-bot"

echo "==> Installing Python dependencies..."
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt"

echo "==> Setting up logrotate for ops-bot audit log..."
sudo tee /etc/logrotate.d/ops-bot > /dev/null <<'EOF'
/home/ubuntu/.ops-bot-audit.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    create 0640 ubuntu ubuntu
}
EOF

echo "==> Installing systemd services..."
sudo cp "$BOT_DIR/deploy/ops-bot.service"       /etc/systemd/system/ops-bot.service
sudo cp "$BOT_DIR/deploy/ops-bot-admin.service" /etc/systemd/system/ops-bot-admin.service
sudo systemctl daemon-reload

echo "==> Enabling and starting services..."
sudo systemctl enable --now ops-bot
sudo systemctl enable --now ops-bot-admin

echo ""
echo "==> Status:"
sudo systemctl status ops-bot       --no-pager -l
sudo systemctl status ops-bot-admin --no-pager -l

echo ""
echo "Done."
echo "  Bot:         sudo journalctl -u ops-bot -f"
echo "  Admin panel: http://<private-ip>:8080  (VPN required)"
echo "  Audit log:   ~/.ops-bot-audit.log  (rotates weekly, 12 weeks)"
