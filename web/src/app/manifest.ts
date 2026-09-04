import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Stem Studio",
    short_name: "Stem Studio",
    description: "Record or edit audio, remove vocals for karaoke, and separate music into useful tracks.",
    start_url: "/",
    display: "standalone",
    background_color: "#070913",
    theme_color: "#070913",
    icons: [
      {
        src: "/icon",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
      {
        src: "/apple-icon",
        sizes: "180x180",
        type: "image/png",
      },
    ],
  };
}
