// TELEGLAS Pro - PM2 Configuration
// Process management for 24/7 uptime

module.exports = {
  apps: [
    {
      name: 'teleglas-pro',
      script: 'venv/bin/python',
      args: 'main.py',
      cwd: '/home/user/teleglas-pro',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production',
        PYTHONUNBUFFERED: '1'
      },
      error_file: 'logs/error.log',
      out_file: 'logs/output.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      min_uptime: '10s',
      max_restarts: 10,
      restart_delay: 4000
    },
    {
      name: 'teleglas-monitor',
      script: 'venv/bin/python',
      args: 'scripts/monitor.py',
      cwd: '/home/user/teleglas-pro',
      instances: 1,
      autorestart: true,
      cron_restart: '0 * * * *',
      watch: false,
      max_memory_restart: '256M'
    }
  ]
};
