/*
 * barcode.js
 * Renders a Code128 barcode entirely in the browser (JsBarcode) for a
 * student's barcode_value — replacing the "printed barcode on plastic ID
 * card" workflow with an on-screen / printable-PDF barcode that needs no
 * card printer or scanner hardware to produce.
 */
function renderBarcode(elementId, value) {
  if (typeof JsBarcode === "undefined") return;
  JsBarcode("#" + elementId, value, {
    format: "CODE128",
    lineColor: "#121826",
    width: 2,
    height: 60,
    displayValue: true,
    fontSize: 14,
    margin: 8,
  });
}
