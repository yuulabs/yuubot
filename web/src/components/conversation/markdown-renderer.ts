import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

export const markdownPlugins = {
  // lazy: remark-gfm enables GFM pipe tables, strikethrough, autolinks,
  // and task-list items. Without it, `| a | b |` tables render as bare text.
  // Add `remark-github-breaks` etc. if more GFM features are needed.
  remark: [remarkGfm, remarkMath],
  rehype: [rehypeKatex],
};
