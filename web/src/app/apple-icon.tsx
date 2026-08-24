import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    <div
      style={{
        alignItems: "center",
        background: "linear-gradient(145deg, #7c3aed, #2563eb)",
        color: "white",
        display: "flex",
        fontSize: 60,
        fontWeight: 800,
        height: "100%",
        justifyContent: "center",
        letterSpacing: -7,
        paddingRight: 7,
        width: "100%",
      }}
    >
      SS
    </div>,
    size,
  );
}
