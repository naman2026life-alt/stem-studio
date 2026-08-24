import { ImageResponse } from "next/og";

export const size = { width: 512, height: 512 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    <div
      style={{
        alignItems: "center",
        background: "linear-gradient(145deg, #7c3aed, #2563eb)",
        color: "white",
        display: "flex",
        fontSize: 168,
        fontWeight: 800,
        height: "100%",
        justifyContent: "center",
        letterSpacing: -18,
        paddingRight: 18,
        width: "100%",
      }}
    >
      SS
    </div>,
    size,
  );
}
