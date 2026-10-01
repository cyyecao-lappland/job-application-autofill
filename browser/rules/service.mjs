/** Registry supplies rule -> control links. Multiple rules may share a control. */
export function matchRegistered(field, entries, route) {
  const found=[...entries.values()].filter(entry=>entry.rules.some(rule=>rule.route===route&&rule.match(field)));
  if(found.length>1)throw new Error('AMBIGUOUS_ADAPTER');
  return found[0]??null;
}
