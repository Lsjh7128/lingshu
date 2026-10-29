// dsh-lingshu —— 浏览器端半边
//
// 把「人格体中枢」作为 LINGSHU 插件自带的一个全局面板挂进 DSH：
//   · 左侧栏 sidebar.panellist 注册入口（排在「自动化任务」之后）
//   · 中心主区域 main 注册 lingshu-persona 页面
//
// 页面本体由宿主半边（dashboard.js）在 /dsh-lingshu/dashboard 提供，
// 这里用 iframe 嵌入，避免把面板的 React/Tailwind/Framer 打包进宿主渲染树。
// 面板当前仍是 Mock 设计态（见 lingshu-persona-dashboard/README.md）。

window.__ModuleLoader__.load({
  id: "dsh-lingshu",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    const jsx = require("react/jsx-runtime");

    const PANEL_ID = "lingshu-persona";
    const DASHBOARD_SRC = "/dsh-lingshu/dashboard";

    /** 侧栏图标：由 lingshu/assets/logo.svg 的六边形—眼—循环箭头简化而来。 */
    function LingshuIcon({ size = 16 } = {}) {
      return jsx.jsx("svg", {
        width: size,
        height: size,
        viewBox: "0 0 16 16",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 1.05,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        "aria-hidden": true,
        children: [
          jsx.jsx("path", {
            d: "M8 1.4 13.7 4.7v6.6L8 14.6 2.3 11.3V4.7Z",
          }, "hex"),
          jsx.jsx("path", {
            d: "M3.6 8.1c1.3-1.8 2.8-2.7 4.4-2.7s3.1.9 4.4 2.7c-1.3 1.8-2.8 2.7-4.4 2.7S4.9 9.9 3.6 8.1Z",
          }, "eye"),
          jsx.jsx("path", {
            d: "M6.4 8.1a1.6 1.6 0 0 1 3.2 0",
          }, "loop"),
        ],
      });
    }

    /** 中心面板：留出完整高度给 iframe，避免 DSH 的 main 插槽高度不传导。 */
    function LingshuDashboardPage() {
      return jsx.jsx("div", {
        "data-testid": "lingshu-dashboard-frame",
        style: {
          width: "100%",
          height: "100%",
          minHeight: "calc(100vh - 80px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--dsw-alias-bg-base, #f3f4ff)",
        },
        children: jsx.jsx("iframe", {
          src: DASHBOARD_SRC,
          title: "LINGSHU 人格体中枢",
          allow: "clipboard-write",
          style: {
            flex: "1 1 auto",
            width: "100%",
            minHeight: 0,
            border: 0,
            display: "block",
            background: "#f3f4ff",
          },
        }),
      });
    }

    const inject = ["slots"];

    function apply(ctx) {
      // 与官方 schedule 面板同一套注册方式：入口在 sidebar.panellist，页面在 main。
      // order 11 = 紧跟在「自动化任务」（order 10）之后。
      ctx.slots.inject("main", () => ctx.slots.register({
        name: "main",
        key: PANEL_ID,
      }, LingshuDashboardPage));

      ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
        name: "sidebar.panellist",
        id: PANEL_ID,
        order: 11,
        label: () => "人格体中枢",
      }, LingshuIcon));
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  },
});
