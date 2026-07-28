/**
 * Typed client for the milestone-1 API (`docs/api-contract.md`).
 *
 * Two rules run through the whole file:
 *
 * 1. **Decimal-valued fields are `string`, never `number`.** `max_points`, `percentage`,
 *    `bonus_points`, `grade` and any points value cross the wire as JSON strings and stay
 *    strings here. A JSON number is parsed into an IEEE-754 double by `JSON.parse`, which
 *    both loses the exactness §7.0 requires and destroys significant trailing zeros
 *    ("12.50" -> 12.5). Nothing in this frontend ever converts them; only
 *    `src/grading/preview.ts` computes with them, on an explicit bigint scale.
 * 2. **The session is an HttpOnly cookie.** Every request sends `credentials: "same-origin"`
 *    and the token is never read, copied or stored in JS/localStorage — that is the point of
 *    HttpOnly. Same-origin is enough because dev proxies `/api` and prod serves one origin.
 */

/* ------------------------------------------------------------------ types */

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
}

/** Admin listing shape — the user rows plus the account-management fields. */
export interface AdminUser extends User {
  is_active: boolean;
  /** ISO timestamp from the server; formatted for display with util/format formatDate. */
  created_at: string;
}

export interface LectureSummary {
  id: number;
  name: string;
  created_at: string;
  exam_count: number;
}

/** `GET /api/lectures/{id}` — "lecture + its exams (summary shape)". */
export interface LectureDetail {
  id: number;
  name: string;
  created_at: string;
  exam_count?: number;
  exams: ExamSummary[];
}

/** §7.3. */
export type BonusMode = "ALWAYS" | "ONLY_IF_PASSING_WITHOUT_BONUS";

export interface Exercise {
  /** Absent for a row the client just added; the server assigns it. */
  id?: number;
  name: string;
  /** DECIMAL — string on purpose, see the file header. Example: "12.5". */
  max_points: string;
  position: number;
}

export interface GradingSchemaRow {
  /** DECIMAL — one of the ten §7.1 grades as a string, e.g. "1.3". Never a JSON number. */
  grade: string;
  /** DECIMAL — percentage of the exam's total points, e.g. "95" or "62.5". */
  percentage: string;
}

export interface ExamSummary {
  id: number;
  lecture_id: number;
  lecture_name: string;
  semester: string;
  termin: string;
  /** "YYYY-MM-DD" or null on the wire; German DD.MM.YYYY is applied in the UI only. */
  exam_date: string | null;
  bonus_mode: BonusMode;
  owner_id: number;
}

export interface ExamDetail extends ExamSummary {
  exercises: Exercise[];
  grading_schema: GradingSchemaRow[];
  registration_count: number;
}

/** Body for POST/PATCH on exams. Exercises/schema are a full replace, never a merge. */
export interface ExamWriteBody {
  semester?: string;
  termin?: string;
  exam_date?: string | null;
  bonus_mode?: BonusMode;
  exercises?: Exercise[];
  grading_schema?: GradingSchemaRow[];
  owner_id?: number;
}

/* ------------------------------------------------------------------ errors */

/**
 * The single error type for every failed request. Carries the HTTP status plus the parsed
 * `detail`, normalised into a list of German messages that can be rendered verbatim.
 *
 * Three `detail` shapes exist in the wild here:
 *   - FastAPI default:            {"detail": "Ungültige Zugangsdaten."}
 *   - contract's validation form: {"detail": {"errors": ["…", "…"]}}   (§7.2 messages)
 *   - Pydantic's own 422:         {"detail": [{"loc": [...], "msg": "...", ...}]}
 */
export class ApiError extends Error {
  readonly status: number;
  /** Server-provided German messages; never empty (falls back to a generic message). */
  readonly messages: string[];
  /** The raw parsed body, kept for debugging. Never rendered directly. */
  readonly body: unknown;

  constructor(status: number, messages: string[], body: unknown) {
    const text = messages.length > 0 ? messages.join(" ") : `HTTP ${status}`;
    super(text);
    this.name = "ApiError";
    this.status = status;
    this.messages = messages.length > 0 ? messages : [text];
    this.body = body;
  }

  /** True for "not logged in" — treated as a state, not an error, by AuthContext. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

/**
 * German messages for anything thrown by this client, for direct display. Server-provided
 * text is passed through verbatim — the backend owns the wording of §7.2's validation errors
 * and of the login failure.
 */
export function errorMessages(error: unknown): string[] {
  if (error instanceof ApiError) return error.messages;
  return ["Unerwarteter Fehler. Bitte erneut versuchen."];
}

function extractMessages(status: number, body: unknown): string[] {
  const detail = isRecord(body) ? body["detail"] : undefined;

  if (typeof detail === "string" && detail.trim() !== "") return [detail];

  if (isRecord(detail)) {
    const errors = detail["errors"];
    if (Array.isArray(errors)) {
      const messages = errors.filter((e): e is string => typeof e === "string");
      if (messages.length > 0) return messages;
    }
  }

  if (Array.isArray(detail)) {
    // Pydantic's default 422: [{loc, msg, type}, ...]
    const messages = detail
      .map((entry) => (isRecord(entry) && typeof entry["msg"] === "string" ? entry["msg"] : null))
      .filter((msg): msg is string => msg !== null);
    if (messages.length > 0) return messages;
  }

  return [fallbackMessage(status)];
}

function fallbackMessage(status: number): string {
  switch (status) {
    case 400:
      return "Die Anfrage wurde abgelehnt.";
    case 401:
      return "Nicht angemeldet.";
    case 403:
      return "Keine Berechtigung für diese Aktion.";
    case 404:
      return "Nicht gefunden.";
    case 409:
      return "Konflikt: Die Aktion wurde nicht ausgeführt.";
    case 422:
      return "Die Eingaben sind ungültig.";
    default:
      return `Serverfehler (HTTP ${status}).`;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * One competing occurrence of a duplicate Matrikelnummer, as carried in a `422`'s
 * `detail.duplicates[].occurrences` (see `_duplicate_occurrences` in
 * `app/api/registrations.py`). Additive to `ApiError.messages`, which already contains the
 * German sentence describing this — this is only for a UI that wants to render it as a table.
 */
export interface DuplicateOccurrence {
  source: "upload" | "database";
  filename: string | null;
  course_code: string;
  module_title: string;
  registration_id: number | null;
}

export interface DuplicateMatrikelnummer {
  matrikelnummer: string;
  occurrences: DuplicateOccurrence[];
}

/**
 * Reads `detail.duplicates` off an `ApiError.body` when the import route rejected on a
 * duplicate Matrikelnummer (§5.3). Returns `null` for anything else — every field is narrowed
 * with a type guard so nothing but known scalars is ever rendered from it.
 */
export function extractDuplicates(error: unknown): DuplicateMatrikelnummer[] | null {
  if (!(error instanceof ApiError)) return null;
  const body = error.body;
  const detail = isRecord(body) ? body["detail"] : undefined;
  const duplicates = isRecord(detail) ? detail["duplicates"] : undefined;
  if (!Array.isArray(duplicates)) return null;

  const result: DuplicateMatrikelnummer[] = [];
  for (const entry of duplicates) {
    if (!isRecord(entry)) continue;
    const matrikelnummer = entry["matrikelnummer"];
    const occurrencesRaw = entry["occurrences"];
    if (typeof matrikelnummer !== "string" || !Array.isArray(occurrencesRaw)) continue;
    const occurrences: DuplicateOccurrence[] = [];
    for (const occurrence of occurrencesRaw) {
      if (!isRecord(occurrence)) continue;
      const source = occurrence["source"];
      const courseCode = occurrence["course_code"];
      const moduleTitle = occurrence["module_title"];
      if (
        (source !== "upload" && source !== "database") ||
        typeof courseCode !== "string" ||
        typeof moduleTitle !== "string"
      ) {
        continue;
      }
      const filename = occurrence["filename"];
      const registrationId = occurrence["registration_id"];
      occurrences.push({
        source,
        filename: typeof filename === "string" ? filename : null,
        course_code: courseCode,
        module_title: moduleTitle,
        registration_id: typeof registrationId === "number" ? registrationId : null,
      });
    }
    result.push({ matrikelnummer, occurrences });
  }
  return result;
}

/* ------------------------------------------------------------------ request */

const BASE = "/api";

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const hasBody = options.body !== undefined;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      // The session cookie is HttpOnly; this is the only way it reaches the server, and the
      // only credential handling this app does.
      credentials: "same-origin",
      headers: hasBody
        ? { "Content-Type": "application/json", Accept: "application/json" }
        : { Accept: "application/json" },
      body: hasBody ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new ApiError(0, ["Der Server ist nicht erreichbar."], null);
  }

  if (response.status === 204) return undefined as T;

  const raw = await response.text();
  let parsed: unknown = null;
  if (raw !== "") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, extractMessages(response.status, parsed), parsed);
  }

  return parsed as T;
}

/* ------------------------------------------------------------------ auth */

export function login(username: string, password: string): Promise<User> {
  return request<User>("/auth/login", { method: "POST", body: { username, password } });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

export function me(): Promise<User> {
  return request<User>("/auth/me");
}

export function changePassword(current_password: string, new_password: string): Promise<void> {
  return request<void>("/auth/password", {
    method: "POST",
    body: { current_password, new_password },
  });
}

/* ------------------------------------------------------------------ lectures */

export function listLectures(): Promise<LectureSummary[]> {
  return request<LectureSummary[]>("/lectures");
}

export function createLecture(name: string): Promise<LectureSummary> {
  return request<LectureSummary>("/lectures", { method: "POST", body: { name } });
}

export function getLecture(id: number): Promise<LectureDetail> {
  return request<LectureDetail>(`/lectures/${id}`);
}

export function updateLecture(id: number, name: string): Promise<LectureSummary> {
  return request<LectureSummary>(`/lectures/${id}`, { method: "PATCH", body: { name } });
}

/**
 * Destructive: cascades to every exam of this lecture and all their grades (§13). The API
 * refuses with 409 unless `?confirm=true` is passed, so the caller must have shown a
 * confirmation dialog first.
 */
export function deleteLecture(id: number): Promise<void> {
  return request<void>(`/lectures/${id}?confirm=true`, { method: "DELETE" });
}

/* ------------------------------------------------------------------ exams */

export function listExams(lectureId: number): Promise<ExamSummary[]> {
  return request<ExamSummary[]>(`/exams?lecture_id=${lectureId}`);
}

/**
 * Omitting `exercises`/`grading_schema` makes the server copy them forward from the lecture's
 * most recent prior exam (§4) — a one-time copy. Pass them only to override that.
 */
export function createExam(lectureId: number, body: ExamWriteBody): Promise<ExamDetail> {
  return request<ExamDetail>(`/lectures/${lectureId}/exams`, { method: "POST", body });
}

export function getExam(id: number): Promise<ExamDetail> {
  return request<ExamDetail>(`/exams/${id}`);
}

export function updateExam(id: number, body: ExamWriteBody): Promise<ExamDetail> {
  return request<ExamDetail>(`/exams/${id}`, { method: "PATCH", body });
}

/** Destructive: cascades to registrations and points (§13); needs the same confirmation. */
export function deleteExam(id: number): Promise<void> {
  return request<void>(`/exams/${id}?confirm=true`, { method: "DELETE" });
}

/* ------------------------------------------------------------------ admin */

export function listUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>("/admin/users");
}

export function createUser(body: {
  username: string;
  password: string;
  is_admin?: boolean;
}): Promise<AdminUser> {
  return request<AdminUser>("/admin/users", { method: "POST", body });
}

export function updateUser(
  id: number,
  body: { is_active?: boolean; is_admin?: boolean },
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body });
}

/** Admin password reset; also kills that user's sessions server-side. */
export function resetUserPassword(id: number, new_password: string): Promise<void> {
  return request<void>(`/admin/users/${id}/password`, {
    method: "POST",
    body: { new_password },
  });
}

/* ------------------------------------------------------------------ registrations (§5, §6) */

export interface RegistrationOut {
  id: number;
  exam_id: number;
  matrikelnummer: string;
  nachname: string;
  vorname: string;
  course_code: string;
  module_title: string;
  versuch: number;
  kommentar: string | null;
  flagged: boolean;
  excluded: boolean;
  attended: boolean | null;
  /** DECIMAL — string on purpose, see the file header. Not edited in this milestone. */
  bonus_points: string;
  source_filename: string | null;
}

/** Body for the manual "late registration" add (§5.3). `course_code`/`module_title` required. */
export interface RegistrationCreateBody {
  matrikelnummer: string;
  nachname: string;
  vorname: string;
  course_code: string;
  module_title: string;
  versuch: number;
  kommentar?: string | null;
  flagged?: boolean;
  excluded?: boolean;
}

/**
 * Body for `PATCH /api/registrations/{id}`. Every field is optional and, per the contract,
 * only the fields actually present are changed server-side (`model_fields_set`) — so callers
 * must omit a field rather than send an empty string/false for "leave unchanged". `bonus_points`
 * is typed `string` and must never be computed from a JS number (§7.0); it is not surfaced in
 * this milestone's UI, but the type still forbids a `number` at the call site.
 */
export interface RegistrationUpdateBody {
  matrikelnummer?: string;
  nachname?: string;
  vorname?: string;
  course_code?: string;
  module_title?: string;
  versuch?: number;
  kommentar?: string | null;
  flagged?: boolean;
  excluded?: boolean;
  attended?: boolean | null;
  bonus_points?: string;
}

export interface ImportedFileSummary {
  filename: string;
  course_code: string;
  module_title: string;
  semester: string;
  termin: string;
  row_count: number;
  flagged_count: number;
  engine: string;
}

export interface RegistrationImportResult {
  imported_total: number;
  replaced_count: number;
  files: ImportedFileSummary[];
  warnings: string[];
}

export interface CourseHeadCount {
  course_code: string;
  count: number;
}

/** §6: the print-count, shown without generating the attendance-list PDF. Excludes excluded. */
export interface RegistrationHeadCount {
  total: number;
  per_course: CourseHeadCount[];
}

/**
 * Every registration of the exam (excluded included — §5.3 keeps them for audit), German-
 * collated by the server (course, then Nachname, then Vorname). Never re-sorted here: a
 * client-side Matr.-Nr. sort would diverge from the §6 attendance-list ordering, which is the
 * authoritative one.
 */
export function listRegistrations(
  examId: number,
  opts: { courseCode?: string } = {},
): Promise<RegistrationOut[]> {
  const query =
    opts.courseCode !== undefined ? `?course_code=${encodeURIComponent(opts.courseCode)}` : "";
  return request<RegistrationOut[]>(`/exams/${examId}/registrations${query}`);
}

export function countRegistrations(examId: number): Promise<RegistrationHeadCount> {
  return request<RegistrationHeadCount>(`/exams/${examId}/registrations/count`);
}

export function createRegistration(
  examId: number,
  body: RegistrationCreateBody,
): Promise<RegistrationOut> {
  return request<RegistrationOut>(`/exams/${examId}/registrations`, { method: "POST", body });
}

export function updateRegistration(
  id: number,
  body: RegistrationUpdateBody,
): Promise<RegistrationOut> {
  return request<RegistrationOut>(`/registrations/${id}`, { method: "PATCH", body });
}

export function deleteRegistration(id: number): Promise<void> {
  return request<void>(`/registrations/${id}`, { method: "DELETE" });
}

/**
 * `POST /exams/{id}/registrations/import` — `multipart/form-data`, field name `files`
 * (repeatable). Bypasses `request()` on purpose: that helper always sets
 * `Content-Type: application/json`, and a multipart body must let the browser set
 * `Content-Type: multipart/form-data; boundary=...` itself — setting it by hand here would
 * omit/break the boundary and the server would fail to parse the parts.
 */
export async function importRegistrations(
  examId: number,
  files: File[],
  replaceExisting: boolean,
): Promise<RegistrationImportResult> {
  const formData = new FormData();
  for (const file of files) formData.append("files", file);
  formData.append("replace_existing", replaceExisting ? "true" : "false");

  let response: Response;
  try {
    response = await fetch(`${BASE}/exams/${examId}/registrations/import`, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      body: formData,
    });
  } catch {
    throw new ApiError(0, ["Der Server ist nicht erreichbar."], null);
  }

  const parsed = await parseJsonBody(response);
  if (!response.ok) {
    throw new ApiError(response.status, extractMessages(response.status, parsed), parsed);
  }
  return parsed as RegistrationImportResult;
}

/** A downloaded binary report plus the filename the server declared for it. */
export interface DownloadedFile {
  blob: Blob;
  filename: string;
}

/**
 * `GET /exams/{id}/reports/attendance-list` — a PDF (§6), not JSON, so this bypasses `request()`
 * entirely and reads the body as a `Blob`. The filename comes from the response's
 * `Content-Disposition` header (`attachment; filename="..."; filename*=UTF-8''...`); the RFC
 * 5987 `filename*` part is preferred since it carries German characters correctly.
 */
export async function downloadAttendanceList(examId: number): Promise<DownloadedFile> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/exams/${examId}/reports/attendance-list`, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/pdf" },
    });
  } catch {
    throw new ApiError(0, ["Der Server ist nicht erreichbar."], null);
  }

  if (!response.ok) {
    const parsed = await parseJsonBody(response);
    throw new ApiError(response.status, extractMessages(response.status, parsed), parsed);
  }

  const blob = await response.blob();
  const filename =
    filenameFromContentDisposition(response.headers.get("Content-Disposition")) ??
    "anwesenheitsliste.pdf";
  return { blob, filename };
}

async function parseJsonBody(response: Response): Promise<unknown> {
  const raw = await response.text();
  if (raw === "") return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function filenameFromContentDisposition(header: string | null): string | null {
  if (header === null) return null;
  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(header);
  const encoded = utf8Match?.[1];
  if (encoded !== undefined) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // fall through to the ASCII fallback below
    }
  }
  const asciiMatch = /filename="?([^";]+)"?/i.exec(header);
  return asciiMatch?.[1] ?? null;
}
