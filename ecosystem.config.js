module.exports = {
  apps: [{
    name: "ductor",
    script: "python",
    args: "-m ductor_bot",
    cwd: __dirname,
    exec_mode: "fork",
    instances: 1,
    autorestart: true,
    restart_delay: 10000,
    exp_backoff_restart_delay: 1000,
    kill_timeout: 5000,
    env: {
      PYTHONUNBUFFERED: "1",
      NODE_ENV: "production"
    },
    // Ductor writes its own bounded rotating agent.log.
    disable_logs: true
  }]
};
