/**
 * CLI render script for Real-Render Remotion compositions.
 *
 * Usage:
 *   npx ts-node src/render.ts --input /path/to/raw.mp4 --output /path/to/final.mp4 \
 *       --address "123 Main St" --type walkthrough [--agent "Agent Name"]
 *
 *   npx ts-node src/render.ts --input /path/to/raw.mp4 --output /path/to/output_dir/ \
 *       --address "123 Main St" --type carousel
 */

import { bundle } from "@remotion/bundler";
import { renderMedia, getCompositions } from "@remotion/renderer";
import path from "path";
import fs from "fs";

// ---------------------------------------------------------------------------
// Argument parsing
// ---------------------------------------------------------------------------

function parseArgs(): {
  input: string;
  output: string;
  address: string;
  type: "walkthrough" | "carousel";
  agent: string;
  videoDuration: number;
} {
  const args = process.argv.slice(2);
  const map: Record<string, string> = {};

  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--") && i + 1 < args.length) {
      map[args[i].slice(2)] = args[i + 1];
      i++;
    }
  }

  if (!map.input || !map.output || !map.address || !map.type) {
    console.error(
      "Usage: npx ts-node src/render.ts --input <raw.mp4> --output <out.mp4|dir> --address <addr> --type <walkthrough|carousel> [--agent <name>] [--duration <secs>]"
    );
    process.exit(1);
  }

  return {
    input: path.resolve(map.input),
    output: path.resolve(map.output),
    address: map.address,
    type: map.type as "walkthrough" | "carousel",
    agent: map.agent || "",
    videoDuration: parseFloat(map.duration || "10"),
  };
}

// ---------------------------------------------------------------------------
// Get video duration via Remotion's probe (falls back to provided value)
// ---------------------------------------------------------------------------

async function probeVideoDuration(filePath: string): Promise<number> {
  // We try to use ffprobe if available, otherwise fall back to the provided duration
  const { execSync } = require("child_process");
  try {
    const result = execSync(
      `ffprobe -v error -show_entries format=duration -of csv=p=0 "${filePath}"`,
      { encoding: "utf-8", timeout: 10000 }
    ).trim();
    const dur = parseFloat(result);
    if (!isNaN(dur) && dur > 0) return dur;
  } catch {
    // ffprobe not available, use fallback
  }
  return 0;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const opts = parseArgs();

  // Validate input file exists
  if (!fs.existsSync(opts.input)) {
    console.error(`Input file not found: ${opts.input}`);
    process.exit(1);
  }

  // Probe actual video duration
  const probedDuration = await probeVideoDuration(opts.input);
  const videoDuration =
    probedDuration > 0 ? probedDuration : opts.videoDuration;

  console.log(`Bundling Remotion project...`);
  const bundleLocation = await bundle({
    entryPoint: path.resolve(__dirname, "./index.ts"),
    webpackOverride: (config) => config,
  });

  const compositions = await getCompositions(bundleLocation);

  if (opts.type === "walkthrough") {
    const composition = compositions.find((c) => c.id === "WalkthroughVideo");
    if (!composition) {
      console.error("WalkthroughVideo composition not found");
      process.exit(1);
    }

    const inputProps = {
      videoSrc: opts.input,
      propertyAddress: opts.address,
      agentName: opts.agent,
      videoDurationInSeconds: videoDuration,
    };

    // Recalculate duration: 3s intro + video + 3s outro
    const totalFrames = Math.ceil((6 + videoDuration) * 30);

    console.log(
      `Rendering walkthrough (${videoDuration.toFixed(1)}s video, ${totalFrames} total frames)...`
    );

    await renderMedia({
      composition: { ...composition, durationInFrames: totalFrames },
      serveUrl: bundleLocation,
      codec: "h264",
      outputLocation: opts.output,
      inputProps,
    });

    console.log(`Rendered walkthrough to: ${opts.output}`);
  } else if (opts.type === "carousel") {
    // Render 5 carousel clips
    const composition = compositions.find(
      (c) => c.id === "InstagramCarousel"
    );
    if (!composition) {
      console.error("InstagramCarousel composition not found");
      process.exit(1);
    }

    // Ensure output directory exists
    fs.mkdirSync(opts.output, { recursive: true });

    const roomLabels = [
      "Living Room",
      "Kitchen",
      "Primary Bedroom",
      "Bathroom",
      "Outdoor Space",
    ];

    const clipDurationFrames = 4 * 30; // 4 seconds at 30fps

    const outputPaths: string[] = [];

    for (let i = 0; i < 5; i++) {
      const clipOutput = path.join(opts.output, `carousel_${i + 1}.mp4`);

      const inputProps = {
        videoSrc: opts.input,
        propertyAddress: opts.address,
        clipIndex: i,
        roomLabel: roomLabels[i],
        videoDurationInSeconds: videoDuration,
      };

      console.log(`Rendering carousel clip ${i + 1}/5: ${roomLabels[i]}...`);

      await renderMedia({
        composition: {
          ...composition,
          durationInFrames: clipDurationFrames,
        },
        serveUrl: bundleLocation,
        codec: "h264",
        outputLocation: clipOutput,
        inputProps,
      });

      outputPaths.push(clipOutput);
    }

    console.log(`Rendered ${outputPaths.length} carousel clips to: ${opts.output}`);

    // Write manifest
    const manifest = {
      type: "instagram_carousel",
      clips: outputPaths,
      address: opts.address,
    };
    fs.writeFileSync(
      path.join(opts.output, "manifest.json"),
      JSON.stringify(manifest, null, 2)
    );
  }

  console.log("Done.");
}

main().catch((err) => {
  console.error("Render failed:", err);
  process.exit(1);
});
