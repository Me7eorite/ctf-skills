import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const baseConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
};

export default function config(phase) {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      ...baseConfig,
      async rewrites() {
        return [
          {
            source: "/api/:path*",
            destination: "http://127.0.0.1:4173/api/:path*",
          },
        ];
      },
    };
  }
  return baseConfig;
}
