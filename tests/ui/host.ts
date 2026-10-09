import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
const w = window as any;
w.events = [];
const log = (e: string, d?: unknown) => w.events.push([e, d]);
w.runHost = async (html: string, args: unknown) => {
  const iframe = document.createElement("iframe");
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.style.cssText = "width:820px;height:0;border:0"; // like a host before size-changed
  document.body.appendChild(iframe);
  const bridge = new AppBridge(null as any, { name: "real-host", version: "1" }, { openLinks: {} },
    { hostContext: { theme: "light", platform: "web", containerDimensions: { maxHeight: 2000, width: 820 } } });
  bridge.oninitialized = () => { log("initialized"); bridge.sendToolInput({ arguments: args as any }); };
  bridge.onsizechange = async (p) => { log("size", p); if (p.height) iframe.style.height = p.height + "px"; };
  bridge.onmessage = async (p) => { log("message", p); return {}; };
  (bridge as any).onerror = (e: unknown) => log("bridge-error", String(e));
  const loaded = new Promise((r) => (iframe.onload = r));
  iframe.srcdoc = html;
  await loaded;
  await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
  log("connected");
};
