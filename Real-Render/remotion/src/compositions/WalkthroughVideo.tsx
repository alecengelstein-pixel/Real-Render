import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";

export type WalkthroughVideoProps = {
  videoSrc: string;
  propertyAddress: string;
  agentName: string;
  videoDurationInSeconds: number;
};

const INTRO_DURATION_SECS = 3;
const OUTRO_DURATION_SECS = 3;

const BrandedIntro: React.FC<{
  propertyAddress: string;
  agentName: string;
}> = ({ propertyAddress, agentName }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoOpacity = interpolate(frame, [0, fps * 0.8], [0, 1], {
    extrapolateRight: "clamp",
  });

  const addressOpacity = interpolate(
    frame,
    [fps * 0.6, fps * 1.4],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const agentOpacity = interpolate(
    frame,
    [fps * 1.0, fps * 1.8],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const logoY = spring({
    frame,
    fps,
    from: 20,
    to: 0,
    config: { damping: 15, stiffness: 80 },
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0a0a0a",
        justifyContent: "center",
        alignItems: "center",
        fontFamily:
          "'Playfair Display', 'Georgia', 'Times New Roman', serif",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
          transform: `translateY(${logoY}px)`,
        }}
      >
        {/* Logo line accent */}
        <div
          style={{
            width: interpolate(frame, [0, fps * 0.6], [0, 80], {
              extrapolateRight: "clamp",
            }),
            height: 2,
            backgroundColor: "#c8a45e",
            opacity: logoOpacity,
          }}
        />

        {/* Brand name */}
        <div
          style={{
            fontSize: 64,
            fontWeight: 700,
            color: "#ffffff",
            letterSpacing: 4,
            opacity: logoOpacity,
            textAlign: "center",
          }}
        >
          OPEN DOOR
        </div>
        <div
          style={{
            fontSize: 28,
            fontWeight: 400,
            color: "#c8a45e",
            letterSpacing: 12,
            opacity: logoOpacity,
            marginTop: -16,
            textAlign: "center",
          }}
        >
          CINEMATIC
        </div>

        {/* Divider */}
        <div
          style={{
            width: interpolate(frame, [fps * 0.4, fps * 1.0], [0, 120], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            height: 1,
            backgroundColor: "#ffffff33",
            marginTop: 8,
          }}
        />

        {/* Property address */}
        <div
          style={{
            fontSize: 32,
            fontWeight: 400,
            color: "#e0e0e0",
            opacity: addressOpacity,
            marginTop: 8,
            textAlign: "center",
            fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
          }}
        >
          {propertyAddress}
        </div>

        {/* Agent name */}
        {agentName && (
          <div
            style={{
              fontSize: 20,
              fontWeight: 300,
              color: "#999999",
              opacity: agentOpacity,
              marginTop: 4,
              textAlign: "center",
              fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
            }}
          >
            Presented by {agentName}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};

const BrandedOutro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Fade in at start, fade out at end
  const fadeIn = interpolate(frame, [0, fps * 0.6], [0, 1], {
    extrapolateRight: "clamp",
  });

  const fadeOut = interpolate(
    frame,
    [durationInFrames - fps * 1.0, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const opacity = Math.min(fadeIn, fadeOut);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0a0a0a",
        justifyContent: "center",
        alignItems: "center",
        fontFamily:
          "'Playfair Display', 'Georgia', 'Times New Roman', serif",
        opacity,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
        }}
      >
        <div
          style={{
            fontSize: 48,
            fontWeight: 700,
            color: "#ffffff",
            letterSpacing: 3,
          }}
        >
          OPEN DOOR
        </div>
        <div
          style={{
            fontSize: 22,
            fontWeight: 400,
            color: "#c8a45e",
            letterSpacing: 10,
            marginTop: -8,
          }}
        >
          CINEMATIC
        </div>

        <div
          style={{
            width: 80,
            height: 1,
            backgroundColor: "#ffffff33",
            marginTop: 16,
          }}
        />

        <div
          style={{
            fontSize: 18,
            fontWeight: 300,
            color: "#888888",
            marginTop: 12,
            fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
            letterSpacing: 2,
          }}
        >
          opendoorcinematic.com
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const WalkthroughVideo: React.FC<WalkthroughVideoProps> = ({
  videoSrc,
  propertyAddress,
  agentName,
  videoDurationInSeconds,
}) => {
  const { fps } = useVideoConfig();
  const introFrames = INTRO_DURATION_SECS * fps;
  const outroFrames = OUTRO_DURATION_SECS * fps;
  const videoFrames = videoDurationInSeconds * fps;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {/* Intro sequence */}
      <Sequence from={0} durationInFrames={introFrames}>
        <BrandedIntro
          propertyAddress={propertyAddress}
          agentName={agentName}
        />
      </Sequence>

      {/* Main walkthrough video */}
      <Sequence from={introFrames} durationInFrames={videoFrames}>
        <AbsoluteFill>
          {videoSrc ? (
            <OffthreadVideo
              src={videoSrc}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <AbsoluteFill
              style={{
                backgroundColor: "#1a1a1a",
                justifyContent: "center",
                alignItems: "center",
                color: "#666",
                fontSize: 24,
                fontFamily: "sans-serif",
              }}
            >
              [Walkthrough Video]
            </AbsoluteFill>
          )}
        </AbsoluteFill>
      </Sequence>

      {/* Outro sequence */}
      <Sequence
        from={introFrames + videoFrames}
        durationInFrames={outroFrames}
      >
        <BrandedOutro />
      </Sequence>
    </AbsoluteFill>
  );
};
