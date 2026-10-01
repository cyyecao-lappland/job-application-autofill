// A logical Add control can move after the first inline record is opened.
// Reacquisition preserves the original collection/count/record-selector contract.
export function resolveCollectionAdd(packet, framework, count) {
  if (!packet.inline_repeater || !['ant','feishu'].includes(framework?.family) || count !== packet.expected_record_count)
    return null;
  const sections = framework.sections.filter(section => section.selector === packet.collection_selector);
  if (sections.length !== 1) return null;
  const section = sections[0];
  if (section.record_selector !== packet.record_selector || section.records?.length !== count
      || section.add_label !== packet.add_label || typeof section.add_selector !== 'string'
      || !section.add_selector.startsWith(':scope > ')) return null;
  return section.add_selector;
}
