// Field selectors produced by readModuleDOM are scoped to the observed module.
// In particular, repeated editors use selectors beginning with `:scope`, which
// are not meaningful from the document root.  Keep the binding rule in one
// place so every adapter resolves the current node from its semantic module.
export function scopedControlTarget(tab,packet){
  return tab.playwright.locator(packet.module_selector).locator(packet.field_selector);
}
