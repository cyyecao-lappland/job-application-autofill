// Site-specific read-only persistence oracle. Never sends a save request or
// exports authentication values / personal identity fields from the page.
export async function readAlibabaEducationPersistence() {
  if (location.origin !== 'https://campus-talent.alibaba.com' || location.pathname !== '/personal/resume') return null;
  const observed = performance.getEntriesByType('resource').find(e => {
    const u = new URL(e.name);
    return u.origin === location.origin && u.pathname === '/resume/detail';
  });
  if (!observed || !window.__sysconfig?.circle || !window.__userconfig) return null;
  // This read endpoint and payload are verified against the site's loaded
  // recruit-portal bundle. POST is the site's detail-query method.
  const response = await fetch(observed.name, {
    method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(10000),
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({channel: window.__sysconfig.circle.portalCampusChannel,
      language: window.__userconfig.locale, _csrf: window.__sysconfig.__token__, code: ''})
  });
  if (!response.ok) return null;
  const result = await response.json();
  if (result.success !== true || !Array.isArray(result.content?.educations)) return null;
  return {oracle: 'alibaba_resume_detail_v1', observed_at: Date.now()/1000,
    education_count: result.content.educations.length};
}

export function confirmsAbsentEducation(packet, persisted) {
  const fields = packet.expected_modules?.flatMap(m => m.fields) || [];
  return packet.target?.url === 'https://campus-talent.alibaba.com/personal/resume'
    && fields.some(f => f.label === '学校全称' && typeof f.value === 'string' && f.value.trim())
    && fields.some(f => f.label === '学历' && f.value)
    && persisted?.oracle === 'alibaba_resume_detail_v1'
    && persisted.education_count === 0
    && persisted.observed_at > 0;
}
