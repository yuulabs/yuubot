import type { MediaSource } from "../types";

/**
 * Renders an image content block.
 * - `[blob:<sha256>]` ref -> fetch from `/api/blobs/<sha256>`
 * - Raw data URI or http(s) URL -> `<img>` directly
 * - Anthropic raw base64 (no data: prefix) -> build data URI from media_type
 */
export function ImageBlock({ source }: { source?: MediaSource | null }) {
  if (!source) return null;

  // OpenAI format passes the whole image_url object: {url: "..."}
  const url: string | undefined = source.url ?? undefined;
  // Anthropic format: {type:"base64", media_type:"...", data:"..."}
  const b64data: string | undefined = source.data ?? undefined;
  const mime: string = source.media_type ?? "image";

  const isBlobRef = (s: string) => s.startsWith("[blob:") && s.endsWith("]");
  const blobUrl = (s: string) => `/api/blobs/${s.slice(6, -1)}`;

  if (url) {
    if (isBlobRef(url)) {
      return <img src={blobUrl(url)} alt="image" style={imgStyle} />;
    }
    return <img src={url} alt="image" style={imgStyle} />;
  }

  if (b64data) {
    if (isBlobRef(b64data)) {
      return <img src={blobUrl(b64data)} alt="image" style={imgStyle} />;
    }
    const src = b64data.startsWith("data:")
      ? b64data
      : `data:${mime};base64,${b64data}`;
    return <img src={src} alt="image" style={imgStyle} />;
  }

  return null;
}

const imgStyle: React.CSSProperties = {
  maxWidth: "100%",
  maxHeight: 320,
  borderRadius: 4,
  border: "1px solid #2d333b",
  display: "block",
};
