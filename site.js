const SMARTLINK =
  "https://armsbroodelusive.com/kyrhzgbcv2?key=d4a2a8fe82dfbe4889dc5d54e3428812";

const adUnits = {
  wide: {
    name: "728 by 90 banner advertisement",
    key: "15c05659f6926726a38a4458f8a45e4c",
    width: 728,
    height: 90,
  },
  medium: {
    name: "468 by 60 banner advertisement",
    key: "9a35cb8267afdbbdc68f8e586a856a4f",
    width: 468,
    height: 60,
  },
  mobile: {
    name: "320 by 50 banner advertisement",
    key: "ed59d7c2405aa0f27b16470082b98a3a",
    width: 320,
    height: 50,
  },
  square: {
    name: "300 by 250 banner advertisement",
    key: "7d8180eb86a306f18e326d14940825c9",
    width: 300,
    height: 250,
  },
  tall: {
    name: "160 by 300 banner advertisement",
    key: "cc1b7dd211c9d1a6416fec545c3a3a35",
    width: 160,
    height: 300,
  },
  skyscraper: {
    name: "160 by 600 banner advertisement",
    key: "ead61481e2ed3805d3ae8e25a35a8f80",
    width: 160,
    height: 600,
  },
};

const centerPlacements = [
  "wide",
  "medium",
  "mobile",
  "square",
  "tall",
  "square",
  "medium",
  "mobile",
  "wide",
  "square",
  "medium",
  "tall",
  "mobile",
  "square",
  "medium",
  "wide",
  "mobile",
  "square",
];

const railPlacements = [
  "skyscraper",
  "tall",
  "skyscraper",
  "tall",
  "skyscraper",
];

function bannerDocument(unit) {
  return `<!doctype html>
<html>
  <head><meta charset="utf-8"></head>
  <body style="margin:0;overflow:hidden;width:${unit.width}px;height:${unit.height}px;background:transparent">
    <script>
      atOptions = {
        'key': '${unit.key}',
        'format': 'iframe',
        'height': ${unit.height},
        'width': ${unit.width},
        'params': {}
      };
    <\/script>
    <script src="https://armsbroodelusive.com/${unit.key}/invoke.js"><\/script>
  </body>
</html>`;
}

function createBanner(placement, label, wideClass = false) {
  const unit = adUnits[placement];
  const cell = document.createElement("div");
  cell.className = `ad-cell ${unit.width > 320 ? "oversize-ad" : ""} ${wideClass ? "wide-ad" : ""}`;
  cell.style.gridColumn = `span ${Math.ceil(unit.width / 160)}`;
  cell.style.gridRow = `span ${Math.ceil(unit.height / 10)}`;

  const frame = document.createElement("iframe");
  frame.className = "ad-frame";
  frame.title = `${unit.name} ${label}`;
  frame.width = String(unit.width);
  frame.height = String(unit.height);
  frame.scrolling = "no";
  frame.srcdoc = bannerDocument(unit);

  cell.append(frame);
  return cell;
}

function createNative(instance) {
  const section = document.createElement("section");
  section.className = "native-ad";
  section.setAttribute("aria-label", "Native banner advertisement");

  const frame = document.createElement("iframe");
  frame.className = "native-frame";
  frame.title = `Native banner advertisement ${instance}`;
  frame.scrolling = "no";
  frame.srcdoc = `<!doctype html>
<html>
  <head><meta charset="utf-8"></head>
  <body style="margin:0;overflow:hidden;background:transparent">
    <script async="async" data-cfasync="false" src="https://armsbroodelusive.com/983a2cce815f060b909feabfe14b2c8d/invoke.js"><\/script>
    <div id="container-983a2cce815f060b909feabfe14b2c8d"></div>
  </body>
</html>`;

  section.append(frame);
  return section;
}

function createSmartlink() {
  const link = document.createElement("a");
  link.className = "smartlink-ad";
  link.href = SMARTLINK;
  link.target = "_blank";
  link.rel = "sponsored noopener noreferrer";
  link.textContent = "Open sponsored offer";
  return link;
}

function renderWall(placements, sectionName) {
  const wall = document.createElement("div");
  wall.className = "ad-wall";

  placements.forEach((placement, index) => {
    wall.append(
      createBanner(
        placement,
        `${sectionName} ${index + 1}`,
        placement === "wide",
      ),
    );
  });

  return wall;
}

function renderRail(element, side) {
  railPlacements.forEach((placement, index) => {
    element.append(createBanner(placement, `${side} ${index + 1}`));
  });
}

const converterFrame = document.querySelector(".converter-frame");
window.addEventListener("message", (event) => {
  if (
    event.source !== converterFrame.contentWindow ||
    event.data?.type !== "questiontemplate:converter-height"
  ) {
    return;
  }

  const height = Math.ceil(Number(event.data.height));
  if (Number.isFinite(height) && height >= 200 && height <= 520) {
    converterFrame.style.height = `${height}px`;
  }
});

renderRail(document.querySelector("#left-rail"), "left");
renderRail(document.querySelector("#right-rail"), "right");

const center = document.querySelector("#center-column");
center.prepend(renderWall(["mobile", "medium"], "above converter"));
center.append(
  renderWall(centerPlacements.slice(0, 6), "top"),
  createNative(1),
  createSmartlink(),
  renderWall(centerPlacements.slice(6, 12), "middle"),
  createNative(2),
  createSmartlink(),
  renderWall(centerPlacements.slice(12), "bottom"),
  createNative(3),
);
