// PM2 process file — bare-metal / VM deploy (house style: eugenia/renan).
// Secrets are NOT put here; they come from `.env` so they don't leak into `pm2 list`.
// Start:  pm2 start ecosystem.config.js
module.exports = {
  apps: [
    {
      name: process.env.AGENT_ID || "assistant",
      cwd: __dirname,
      script: ".venv/bin/uvicorn",
      args: "agent_app.main:app --host 0.0.0.0 --port 8888 --workers 2",
      interpreter: "none",
      exec_mode: "fork",
      max_memory_restart: "1G",
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        // Non-secret runtime config only. Real secrets live in .env (loaded by config.py).
        TZ: "America/Sao_Paulo",
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
