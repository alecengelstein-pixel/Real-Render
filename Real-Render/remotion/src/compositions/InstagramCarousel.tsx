import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
} from "remotion";

export type InstagramCarouselProps = {
  videoSrc: string;
  propertyAddress: string;
  clipIndex: number;
  roomLabel: string;
  videoDurationInSeconds: number;
};

const CLIP_DURATION_SECS = 4;

/**
 * Default room labels used when none are provided.
 * The pipeline can override these via props.
 */
const DEFAULT_ROOM_LABELS = [
  "Living Room",
  "Kitchen",
  "Primary Bedroom",
  "Bathroom",
  "Outdoor Space",
];

export const InstagramCarousel: React.FC<InstagramCarouselProps> = ({
  videoSrc,
  propertyAddress,
  clipIndex,
  roomLabel,
  videoDurationInSeconds,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Calculate the start time in the source video for this clip.
  // We space 5 clips evenly across the video, skipping the first/last 10%.
  const usableDuration = videoDurationInSeconds * 0.8;
  const clipSpacing = usableDuration / 5;
  const startTimeInVideo =
    videoDurationInSeconds * 0.1 + clipIndex * clipSpacing;

  // Fade in the overlay text
  const labelOpacity = interpolate(frame, [fps * 0.3, fps * 0.8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Fade out near the end
  const fadeOut = interpolate(
    frame,
    [durationInFrames - fps * 0.5, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const label = roomLabel || DEFAULT_ROOM_LABELS[clipIndex] || `Room ${clipIndex + 1}`;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {/* Video clip — square crop from the source */}
      {videoSrc ? (
        <OffthreadVideo
          src={videoSrc}
          startFrom={Math.floor(startTimeInVideo * fps)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      ) : (
        <AbsoluteFill
          style={{
            backgroundColor: "#1a1a1a",
            justifyContent: "center",
            alignItems: "center",
            color: "#666",
            fontSize: 20,
            fontFamily: "sans-serif",
          }}
        >
          [Clip {clipIndex + 1}]
        </AbsoluteFill>
      )}

      {/* Bottom gradient overlay */}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(to top, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0) 40%)",
          opacity: fadeOut,
        }}
      />

      {/* Room label overlay */}
      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          alignItems: "flex-start",
          padding: 48,
          opacity: Math.min(labelOpacity, fadeOut),
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div
            style={{
              fontSize: 36,
              fontWeight: 700,
              color: "#ffffff",
              fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
              textShadow: "0 2px 8px rgba(0,0,0,0.5)",
            }}
          >
            {label}
          </div>
          <div
            style={{
              fontSize: 16,
              fontWeight: 400,
              color: "#cccccc",
              fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
              textShadow: "0 1px 4px rgba(0,0,0,0.5)",
            }}
          >
            {propertyAddress}
          </div>
        </div>
      </AbsoluteFill>

      {/* Branding watermark — top right */}
      <AbsoluteFill
        style={{
          justifyContent: "flex-start",
          alignItems: "flex-end",
          padding: 32,
          opacity: 0.6 * fadeOut,
        }}
      >
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: "#c8a45e",
            letterSpacing: 2,
            fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
          }}
        >
          OPEN DOOR CINEMATIC
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
