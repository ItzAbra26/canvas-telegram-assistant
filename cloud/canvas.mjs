export class CanvasError extends Error {
  constructor(status = 0) { super('Canvas unavailable'); this.status = status; }
}
export class InitialCanvas {
  constructor(base, token, cleanHTML, fetcher = fetch) {
    this.base = base; this.token = token; this.cleanHTML = cleanHTML; this.fetcher = (url, init) => fetcher(url, init);
  }
  async get(url) {
    const value = new URL(url);
    if (value.origin !== new URL(this.base).origin || value.protocol !== 'https:' || value.username || value.password || !value.pathname.startsWith('/api/v1/')) throw new CanvasError();
    let response;
    try { response = await this.fetcher(value.href, { headers: { Authorization: 'Bearer ' + this.token }, redirect: 'manual', signal: AbortSignal.timeout(10000) }); }
    catch { throw new CanvasError(); }
    if (response.status !== 200) throw new CanvasError(response.status);
    let body;
    try { body = await response.json(); } catch { throw new CanvasError(); }
    return { body, links: response.headers.get('link') || '' };
  }
  async profile() {
    const { body } = await this.get(this.base + '/api/v1/users/self/profile');
    if (!Number.isSafeInteger(body?.id)) throw new CanvasError();
    return body.id;
  }
  async pages(path, query) {
    let url = this.base + '/api/v1/' + path + '?' + new URLSearchParams(query);
    const visited = new Set(), rows = [];
    while (url) {
      if (visited.has(url) || visited.size >= 1000) throw new CanvasError();
      visited.add(url);
      const { body, links } = await this.get(url);
      if (!Array.isArray(body) || body.some(row => !row || !Number.isSafeInteger(row.id))) throw new CanvasError();
      rows.push(...body);
      const next = links.split(',').find(part => /rel="next"/.test(part));
      const match = next?.match(/<([^>]+)>/);
      url = match ? new URL(match[1], url).href : null;
    }
    return rows;
  }
  async snapshot() {
    const courses = await this.pages('courses', { enrollment_state: 'active', enrollment_type: 'student', 'state[]': 'available', per_page: '100' });
    if (courses.some(c => typeof c.name !== 'string') || new Set(courses.map(c => c.id)).size !== courses.length) throw new CanvasError();
    const assignments = [];
    for (const course of courses) {
      const rows = await this.pages(`courses/${course.id}/assignments`, { 'include[]': 'submission', override_assignment_dates: 'true', per_page: '100' });
      for (const raw of rows) {
        if (raw.published === false) continue;
        let submission = raw.submission;
        if (!submission?.workflow_state) submission = (await this.get(`${this.base}/api/v1/courses/${course.id}/assignments/${raw.id}/submissions/self`)).body;
        if (!['unsubmitted', 'submitted', 'pending_review', 'graded'].includes(submission?.workflow_state) || typeof raw.name !== 'string' || !Array.isArray(raw.submission_types) || !['due_at', 'description', 'points_possible'].every(k => k in raw)) throw new CanvasError();
        const dates = {};
        for (const field of ['created_at', 'unlock_at', 'due_at']) {
          const value = raw[field];
          if (value && (!/([zZ]|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value)))) throw new CanvasError();
          dates[field] = value ? new Date(value).toISOString() : null;
        }
        assignments.push({ id: raw.id, course_id: course.id, course_name: course.name, name: raw.name, description: this.cleanHTML(raw.description || ''), ...dates,
          points: raw.points_possible, url: raw.html_url || `${this.base}/courses/${course.id}/assignments/${raw.id}`,
          submission_state: submission.workflow_state, submitted: Boolean(submission.submitted_at || submission.attempt || ['submitted', 'pending_review'].includes(submission.workflow_state)),
          excused: Boolean(submission.excused), requires_submission: raw.submission_types.some(t => !['none', 'not_graded'].includes(t)),
        });
      }
    }
    if (new Set(assignments.map(a => `${a.course_id}:${a.id}`)).size !== assignments.length) throw new CanvasError();
    return { courses: courses.map(c => ({ id: c.id, name: c.name })), assignments };
  }
}
