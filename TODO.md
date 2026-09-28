# TODO

## Deferred

### Mobile CJK font fallback

**Problem**

部分手机浏览器中的中文正文仍可能明显回退为衬线字体。

**Current behavior**

- Desktop Web 已确认使用无衬线字体。
- Web CSS 已明确设置 `sans-serif` fallback，表单控件也使用 `font: inherit`。
- Production build 已重新生成，且未发现显式的 `font-family: serif` 规则。
- 问题可能来自移动端浏览器或系统的 CJK generic font fallback；现有 font stack 在部分设备上可能未命中合适的中文无衬线字体。

**Possible future direction**

- 使用 DevTools 或 remote debugging 检查手机实际使用的 Rendered Font。
- 分别确认 Android 和 iOS 的系统 CJK sans fallback。
- 如果仍无法稳定控制，再考虑引入项目内置中文无衬线 Web Font；当前阶段不要为此引入大型字体资源。

Status: `Deferred`

### Closed-tab nickname recovery

**Problem**

用户关闭标签页后立即重新进入时，原昵称在 disconnect grace period 内仍可能被占用。

**Current behavior**

- 浏览器仅切到后台或临时断线时，现有 resume token 与 disconnect grace period 可以恢复原身份。
- 直接关闭标签页会使 `sessionStorage` 中的 resume token 随标签页生命周期丢失。
- 服务器仍会在 grace period 内保留原 User 和 nickname；重新打开网址后进入普通登录页，立即使用原昵称会收到“昵称已有人使用”。
- 这是当前匿名 Session 身份模型下的预期边界情况。

暂时不要通过自动踢掉同名旧 session、允许无 token 抢占昵称、调整 grace period、将 resume token 永久保存到 `localStorage`，或增加复杂匿名恢复机制来打补丁。这些方案可能引入冒用、抢占或异常恢复问题。

**Possible future direction**

- 等账号或用户身份系统建立后统一解决。
- 将稳定 user identity 与 nickname 分离。
- 让 reconnect、session ownership 和房间角色占用绑定稳定账号或设备身份，而不是仅绑定 nickname。
- 届时重新设计关闭标签页后的恢复流程。

Status: `Deferred until account / identity system`
