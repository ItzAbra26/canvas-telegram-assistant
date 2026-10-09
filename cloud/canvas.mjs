import { ignored } from './commands.mjs';

export class CanvasError extends Error {
  constructor(status = 0, reason = 'network') { super('Canvas unavailable'); this.status = status; this.reason = reason; }
}
export class InitialCanvas {
  constructor(base, token, cleanHTML, fetcher = fetch) {
    this.base = base; this.token = token; this.cleanHTML = cleanHTML; this.fetcher = (url, init) => fetcher(url, init);
  }
  async request(url, method = 'GET', payload = undefined) {
    const value = new URL(url);
    if (value.origin !== new URL(this.base).origin || value.protocol !== 'https:' || value.username || value.password || !value.pathname.startsWith('/api/v1/')) throw new CanvasError();
    let response;
    try { response = await this.fetcher(value.href, { method, headers: { Authorization: 'Bearer ' + this.token, Accept: 'application/json', 'User-Agent': 'CanvasTelegramAssistant/1.0 (+https://github.com/ItzAbra26/canvas-telegram-assistant)', ...(payload ? { 'Content-Type': 'application/json' } : {}) }, body: payload ? JSON.stringify(payload) : undefined, redirect: 'manual', signal: this.signal ? AbortSignal.any([this.signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000) }); }
    catch { throw new CanvasError(); }
    if (response.status !== 200 && !(method === 'POST' && response.status >= 200 && response.status < 300)) {
      // Retain only an enumerated diagnosis, never provider text or credentials.
      let reason = response.status === 401 ? 'unauthorized' : response.status === 403 ? 'forbidden' : 'http';
      if (response.headers.get('cf-mitigated') === 'challenge') reason = 'challenge';
      else if (response.headers.get('content-type')?.includes('text/html')) reason = 'html-response';
      else {
        try {
          const error = await response.json();
          const description = JSON.stringify(error).toLowerCase();
          if (description.includes('insufficient_scope') || description.includes('insufficient scope')) reason = 'scope';
          else if (description.includes('invalid access token') || description.includes('invalid_token')) reason = 'invalid-token';
        } catch { /* HTTP status remains useful for an incomplete response. */ }
      }
      throw new CanvasError(response.status, reason);
    }
    let body;
    try { body = await response.json(); } catch { throw new CanvasError(); }
    return { body, links: response.headers.get('link') || '' };
  }
  get(url) { return this.request(url); }
  async assignment(course, id) {
    const { body } = await this.get(`${this.base}/api/v1/courses/${course}/assignments/${id}?include[]=submission&override_assignment_dates=true`);
    if (body?.id !== id || body.course_id !== course) throw new CanvasError();
    if (!body.submission) body.submission = (await this.get(`${this.base}/api/v1/courses/${course}/assignments/${id}/submissions/self`)).body;
    if (!['unsubmitted', 'submitted', 'pending_review', 'graded'].includes(body.submission?.workflow_state)) throw new CanvasError();
    return body;
  }
  async upload(course, id, file, blob) {
    const { body } = await this.request(`${this.base}/api/v1/courses/${course}/assignments/${id}/submissions/self/files`, 'POST', { name: file.name, size: blob.size, content_type: file.mime, on_duplicate: 'rename' });
    let url;
    try { url = new URL(body.upload_url); } catch { throw new CanvasError(); }
    // Upload policies come from authenticated Canvas. Never forward its bearer token.
    if (url.protocol !== 'https:' || url.username || url.password || url.port && url.port !== '443' || url.hostname === 'localhost' || !url.hostname.includes('.') || /(^\d+\.\d+\.\d+\.\d+$|:|\.local$|\.internal$)/.test(url.hostname) || !body.upload_params || typeof body.upload_params !== 'object' || Array.isArray(body.upload_params) || Object.keys(body.upload_params).length > 100) throw new CanvasError();
    const form = new FormData();
    for (const [key, value] of Object.entries(body.upload_params)) {
      if (key === 'file' || typeof value !== 'string' || value.length > 10000) throw new CanvasError();
      form.append(key, value);
    }
    form.append('file', blob, file.name); // Canvas requires file to be last.
    let response;
    try { response = await this.fetcher(url.href, { method: 'POST', body: form, redirect: 'manual', signal: this.signal || AbortSignal.timeout(20000) }); } catch { throw new CanvasError(); }
    let uploaded;
    const location = response.headers.get('location');
    if ((response.status >= 300 && response.status < 400 || response.status === 201) && location) {
      const target = new URL(location, url);
      if (target.origin !== new URL(this.base).origin || !target.pathname.startsWith('/api/v1/files/')) throw new CanvasError();
      uploaded = (await this.get(target.href)).body;
    } else if (response.status >= 200 && response.status < 300) {
      try { uploaded = await response.json(); } catch { throw new CanvasError(); }
    } else throw new CanvasError(response.status);
    if (!Number.isSafeInteger(uploaded?.id) || uploaded.id <= 0 || uploaded.size != null && uploaded.size !== blob.size) throw new CanvasError();
    return uploaded.id;
  }
  async submitFile(course, id, fileId, userId) {
    const url = `${this.base}/api/v1/courses/${course}/assignments/${id}/submissions`;
    let result = (await this.request(url, 'POST', { submission: { submission_type: 'online_upload', file_ids: [fileId] } })).body;
    const valid = body => body?.assignment_id === id && body.user_id === userId && ['submitted', 'pending_review', 'graded'].includes(body.workflow_state) && Number.isSafeInteger(body.attempt) && body.attempt > 0 && typeof body.submitted_at === 'string' && Number.isFinite(Date.parse(body.submitted_at)) && body.attachments?.some(file => file.id === fileId);
    if (!valid(result)) {
      try { result = (await this.get(url + '/self')).body; } catch { throw new CanvasError(0, 'unconfirmed'); }
    }
    if (!valid(result)) throw new CanvasError(0, 'unconfirmed');
    return result;
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
    const assignments = [], excluded = {};
    for (const course of courses) {
      const rows = await this.pages(`courses/${course.id}/assignments`, { 'include[]': 'submission', override_assignment_dates: 'true', per_page: '100' });
      for (const raw of rows) {
        if (raw.published === false) continue;
        if (ignored(raw)) { excluded[`${course.id}:${raw.id}`] = raw.name; continue; }
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
    return { courses: courses.map(c => ({ id: c.id, name: c.name })), assignments, ignored: excluded };
  }
}
