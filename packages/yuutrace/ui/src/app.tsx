import { createRoot } from "react-dom/client";
import { TracePage } from "./pages/TracePage";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<TracePage />);
}
