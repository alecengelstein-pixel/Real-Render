import { Composition } from "remotion";
import { WalkthroughVideo } from "./compositions/WalkthroughVideo";
import { InstagramCarousel } from "./compositions/InstagramCarousel";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="WalkthroughVideo"
        component={WalkthroughVideo}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          videoSrc: "",
          propertyAddress: "123 Main Street",
          agentName: "",
          videoDurationInSeconds: 10,
        }}
        calculateMetadata={({ props }) => {
          // 3s intro + video duration + 3s outro, all at 30fps
          const introDuration = 3 * 30;
          const outroDuration = 3 * 30;
          const videoDuration = (props.videoDurationInSeconds || 10) * 30;
          return {
            durationInFrames: introDuration + videoDuration + outroDuration,
          };
        }}
      />
      <Composition
        id="InstagramCarousel"
        component={InstagramCarousel}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1080}
        defaultProps={{
          videoSrc: "",
          propertyAddress: "123 Main Street",
          clipIndex: 0,
          roomLabel: "Living Room",
          videoDurationInSeconds: 10,
        }}
        calculateMetadata={({ props }) => {
          // Each carousel clip is 4 seconds
          return {
            durationInFrames: 4 * 30,
          };
        }}
      />
    </>
  );
};
