#!/bin/bash
# Run once on EC2 after first deploy.

set -e

echo "Setting up logrotate for ops-bot audit log..."

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

echo "Done. Audit log will rotate weekly, keeping 12 weeks of compressed history."
