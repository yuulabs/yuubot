import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkDirective from "remark-directive";
import rehypeKatex from "rehype-katex";

type MarkdownNode = {
  type: string;
  name?: string;
  url?: string;
  children?: MarkdownNode[];
  attributes?: Record<string, string | null | undefined>;
  data?: Record<string, unknown>;
};

function galleryClass(layout: string): string {
  return `markdown-gallery markdown-gallery--${layout}`;
}

function transformGalleryChildren(children: MarkdownNode[]): MarkdownNode[] {
  return children.map((child) => {
    if (child.type === "image" && child.url) {
      return {
        type: "link",
        url: child.url,
        data: { hProperties: { className: ["markdown-gallery__link"] } },
        children: [child],
      };
    }
    if (child.children) {
      child.children = transformGalleryChildren(child.children);
    }
    return child;
  });
}

function imageCount(node: MarkdownNode): number {
  if (node.type === "image") return 1;
  return (node.children ?? []).reduce((count, child) => count + imageCount(child), 0);
}

/** Turn only :::gallery directives into styled containers. Unknown directives
 * are unwrapped so their standard Markdown children remain readable. */
function remarkGallery() {
  return (tree: MarkdownNode) => {
    const visit = (parent: MarkdownNode) => {
      if (!parent.children) return;
      const next: MarkdownNode[] = [];
      for (const child of parent.children) {
        if (child.type === "containerDirective" && child.name === "gallery") {
          const attributes = child.attributes ?? {};
          const requestedLayout = attributes.layout;
          const requestedImageCount = (child.children ?? []).reduce((count, node) => count + imageCount(node), 0);
          const layout = requestedLayout === "collage" && requestedImageCount > 6
            ? "grid"
            : requestedLayout === "collage" || requestedLayout === "grid"
            ? requestedLayout
            : "strip";
          const columns = layout === "grid" && /^[1-6]$/.test(attributes.columns ?? "")
            ? attributes.columns
            : undefined;
          child.data = {
            hName: "div",
            hProperties: {
              className: [galleryClass(layout), `markdown-gallery--count-${requestedImageCount}`],
              ...(columns ? { dataColumns: columns } : {}),
            },
          };
          child.children = transformGalleryChildren(child.children ?? []);
          next.push(child);
        } else if (child.type === "containerDirective") {
          next.push(...(child.children ?? []));
        } else {
          visit(child);
          next.push(child);
        }
      }
      parent.children = next;
    };
    visit(tree);
  };
}

export const markdownPlugins = {
  // lazy: remark-gfm enables GFM pipe tables, strikethrough, autolinks,
  // and task-list items. Without it, `| a | b |` tables render as bare text.
  // Add `remark-github-breaks` etc. if more GFM features are needed.
  remark: [remarkGfm, remarkMath, remarkDirective, remarkGallery],
  rehype: [rehypeKatex],
};
