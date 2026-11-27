import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, "..");
const WEB_DIR = path.join(ROOT_DIR, "apps", "web");
const STUDIO_DIR = path.join(ROOT_DIR, "apps", "studio");
const NEXT_BIN = path.join(WEB_DIR, "node_modules", "next", "dist", "bin", "next");
const TSC_BIN = path.join(WEB_DIR, "node_modules", "typescript", "bin", "tsc");

function checkHostFree(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.on("error", (err) => {
      if (err.code === "EADDRNOTAVAIL" || err.code === "EAFNOSUPPORT") {
        resolve(true);
      } else {
        resolve(false);
      }
    });
    server.listen({ port, host, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
}

async function isPortAvailable(port) {
  if (port === 3000) return false;
  const [v4Loopback, v4Any, v6Loopback] = await Promise.all([
    checkHostFree(port, "127.0.0.1"),
    checkHostFree(port, "0.0.0.0"),
    checkHostFree(port, "::1"),
  ]);
  return v4Loopback && v4Any && v6Loopback;
}

function getEphemeralPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function pickRandomPort(min, max, exclude = new Set([3000])) {
  const span = max - min + 1;
  for (let attempt = 0; attempt < 40; attempt++) {
    const candidate = min + Math.floor(Math.random() * span);
    if (exclude.has(candidate)) continue;
    if (await isPortAvailable(candidate)) {
      return candidate;
    }
  }
  const fallback = await getEphemeralPort();
  if (!exclude.has(fallback)) return fallback;
  return getEphemeralPort();
}

function extractExplicitPort(args, envVarName) {
  const remaining = [];
  let explicitPort = null;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if ((arg === "--port" || arg === "-p") && args[i + 1]) {
      explicitPort = Number(args[i + 1]);
      i += 1;
    } else if (arg.startsWith("--port=")) {
      explicitPort = Number(arg.slice("--port=".length));
    } else if (arg === "--") {
      continue;
    } else {
      remaining.push(arg);
    }
  }

  if (!explicitPort && envVarName && process.env[envVarName]) {
    explicitPort = Number(process.env[envVarName]);
  }

  return {
    port: Number.isInteger(explicitPort) && explicitPort > 0 ? explicitPort : null,
    extraArgs: remaining,
  };
}

function runProcess(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      ...options,
    });
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      if (signal) {
        process.kill(process.pid, signal);
        return;
      }
      resolve(code ?? 0);
    });
  });
}

async function startWeb(mode, rawArgs, excludePorts = new Set([3000])) {
  const { port: requestedPort, extraArgs } = extractExplicitPort(rawArgs, "WEB_PORT");
  const port = requestedPort ?? (await pickRandomPort(3100, 3999, excludePorts));
  const subcommand = mode === "web-start" ? "start" : "dev";

  console.log(`\n[web] Assigned port ${port} → http://localhost:${port}\n`);

  return spawn(
    process.execPath,
    [NEXT_BIN, subcommand, "-p", String(port), ...extraArgs],
    {
      cwd: WEB_DIR,
      stdio: "inherit",
      env: {
        ...process.env,
        PORT: String(port),
        NEXT_PUBLIC_SITE_URL:
          process.env.NEXT_PUBLIC_SITE_URL || `http://localhost:${port}`,
      },
    },
  );
}

async function startStudio(rawArgs, excludePorts = new Set([3000])) {
  const { port: requestedPort, extraArgs } = extractExplicitPort(
    rawArgs,
    "STUDIO_PORT",
  );
  const port = requestedPort ?? (await pickRandomPort(8100, 8999, excludePorts));

  console.log(`\n[studio] Assigned port ${port} → http://127.0.0.1:${port}\n`);

  return spawn(
    "uv",
    [
      "--directory",
      STUDIO_DIR,
      "run",
      "python",
      "-m",
      "video_pipeline.studio",
      "--port",
      String(port),
      ...extraArgs,
    ],
    {
      cwd: ROOT_DIR,
      stdio: "inherit",
    },
  );
}

async function main() {
  const [target = "web", ...rawArgs] = process.argv.slice(2);

  if (target === "web-build") {
    const code = await runProcess(process.execPath, [NEXT_BIN, "build"], {
      cwd: WEB_DIR,
    });
    process.exit(code);
  }

  if (target === "web-typecheck") {
    const genCode = await runProcess(process.execPath, [NEXT_BIN, "typegen"], {
      cwd: WEB_DIR,
    });
    if (genCode !== 0) process.exit(genCode);
    const tscCode = await runProcess(
      process.execPath,
      [TSC_BIN, "--noEmit"],
      { cwd: WEB_DIR },
    );
    process.exit(tscCode);
  }

  if (target === "web" || target === "web-start") {
    const child = await startWeb(target, rawArgs);
    child.on("exit", (code) => process.exit(code ?? 0));
    return;
  }

  if (target === "studio") {
    const child = await startStudio(rawArgs);
    child.on("exit", (code) => process.exit(code ?? 0));
    return;
  }

  if (target === "both") {
    const used = new Set([3000]);
    const webPort = await pickRandomPort(3100, 3999, used);
    used.add(webPort);
    const studioPort = await pickRandomPort(8100, 8999, used);

    const webChild = await startWeb("web", ["--port", String(webPort)]);
    const studioChild = await startStudio(["--port", String(studioPort)]);

    const shutdown = () => {
      webChild.kill("SIGINT");
      studioChild.kill("SIGINT");
    };

    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);

    webChild.on("exit", (code) => {
      if (code && code !== 0) {
        studioChild.kill("SIGINT");
        process.exit(code);
      }
    });
    studioChild.on("exit", (code) => {
      if (code && code !== 0) {
        webChild.kill("SIGINT");
        process.exit(code);
      }
    });
    return;
  }

  console.error(
    `Unknown target "${target}". Expected: web | web-start | web-build | web-typecheck | studio | both`,
  );
  process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
