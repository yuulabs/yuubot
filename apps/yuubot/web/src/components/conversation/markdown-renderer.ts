import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

export const markdownPlugins = {
  remark: [remarkMath],
  rehype: [rehypeKatex],
};
