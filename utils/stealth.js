Object.defineProperty(navigator, "webdriver", {
  get: () => undefined,
});

Object.defineProperty(navigator, "languages", {
  get: () => ["en-US", "en"],
});

Object.defineProperty(navigator, "hardwareConcurrency", {
  get: () => 8,
});

Object.defineProperty(navigator, "deviceMemory", {
  get: () => 8,
});

Object.defineProperty(navigator, "plugins", {
  get: () => [],
});

const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (param) {
  if (param === 37445) return "Google Inc.";
  if (param === 37446)
    return "ANGLE (Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)";
  return getParameter.apply(this, [param]);
};
