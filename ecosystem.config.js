module.exports = {
  apps: [{
    name: "ductor",
    script: "python",
    args: "-m ductor_bot",
    cwd: "D:\\Documents\\GitHub\\ductor",
    exec_mode: "fork",
    instances: 1,
    autorestart: true,
    // Wait 10 seconds before restarting to ensure port 8799 is fully released by the OS
    restart_delay: 10000, 
    // Exponential backoff to prevent rapid fire restarts if things fail hard
    exp_backoff_restart_delay: 1000,
    // Wait for process to clean up its resources
    kill_timeout: 5000,
    env: {
      PYTHONUNBUFFERED: "1",
      NODE_ENV: "production"
    },
    // Log files configuration
    error_file: "C:\\Users\\ZOZN109\\.ductor\\logs\\pm2_error.log",
    out_file: "C:\\Users\\ZOZN109\\.ductor\\logs\\pm2_out.log",
    log_date_format: "YYYY-MM-DD HH:mm:ss",
    merge_logs: true
  }]
}
