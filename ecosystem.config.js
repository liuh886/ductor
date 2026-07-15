const path = require("path");

const python = process.env.DUCTOR_PYTHON || process.env.PYTHON || "python";
const pythonPath = [__dirname, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
const codexHome = process.env.DUCTOR_CODEX_HOME || process.env.CODEX_HOME;
const env = {
  PYTHONUNBUFFERED: "1",
  PYTHONPATH: pythonPath,
  NODE_ENV: "production"
};
if (codexHome) {
  env.CODEX_HOME = codexHome;
}

module.exports = {
  apps: [{
    name: "ductor",
    script: path.join(__dirname, "ductor_bot", "__main__.py"),
    interpreter: python,
    cwd: __dirname,
    exec_mode: "fork",
    instances: 1,
    autorestart: true,
    restart_delay: 10000,
    exp_backoff_restart_delay: 1000,
    kill_timeout: 5000,
    env,
    // Ductor writes its own bounded rotating agent.log.
    disable_logs: true
  }]
};
