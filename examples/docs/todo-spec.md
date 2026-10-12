# Todo App: specification (prose with generated tables)

This document is written by hand, except for the tables between `speclang` markers. Those tables are
generated from the YAML spec and rewritten by `speclint sync`; edit the YAML, never the tables.

```
speclint sync examples/docs/todo-spec.md --spec examples/todo/          # update the tables
speclint sync examples/docs/todo-spec.md --spec examples/todo/ --check  # CI: fail if out of date
```

## 4. Acceptance criteria

Each criterion is a Given/When/Then scenario that a test can check. Written once in the YAML.

<!-- speclang:acceptance -->

| ID | Covers | Given | When | Then |
|---|---|---|---|---|
| AC-1 | FR-1 | an unregistered email and a 12+ character password not in the breach list | the user submits registration | an account is created, a verification email is sent, and the user lands on an empty Inbox |
| AC-2 | FR-1 | a password found in the breach list | the user registers | the request returns 422 WEAK_PASSWORD and no account is created |
| AC-3 | FR-5 | a reset link older than 30 minutes or already used | it is opened | the user sees link expired and can request a new one |
| AC-4 | FR-10 | a signed-in Editor | they add a task with a 1 to 500 character title | it appears at the top of the list within 100 ms and the server confirms it within 500 ms at p95 |
| AC-5 | FR-10 | a title of 501 characters | it is submitted | the field shows an inline error and nothing is saved |
| AC-6 | FR-12, FR-15 | a weekly recurring task | the user completes it | it is marked done and the next occurrence is created with the due date moved forward by 7 days |
| AC-7 | FR-13 | a task deleted within the last 30 days | the user taps Undo or restores it | it returns to its previous list and position |
| AC-8 | FR-30, FR-31 | a Viewer on a shared list | they try to edit a task | the API returns 403 FORBIDDEN_ROLE and the UI shows the task read-only |
| AC-9 | FR-40, FR-41 | a reminder set for 09:00 in the user's time zone | the time arrives | a push is delivered within 60 seconds at the 95th percentile, and an email is sent only if push fails |
| AC-10 | FR-50, FR-52 | the device is offline | the user creates and completes three tasks and then reconnects | all three sync exactly once, in order, with no duplicates, even if the push request is retried |
| AC-11 | FR-51 | the same task title edited on two devices | both sync | the later server-accepted change wins and the overridden value appears in task history |
| AC-12 | FR-61 | a confirmed account deletion request | 30 days have passed | all personal data is erased and the user's content in shared lists is anonymized |
| AC-13 | FR-2 | a user whose email is not verified | they invite a member to a list | the API returns 403 EMAIL_NOT_VERIFIED and no invitation is created |
| AC-14 | FR-3 | a user with a Google account | they complete OAuth sign-in | an account is created or linked and a session starts; a provider email that is not verified is rejected |
| AC-15 | FR-6 | a user with two active sessions | they revoke one of them | its refresh token stops working within 1 minute and the session disappears from the list |
| AC-16 | FR-11 | an Editor and a task at version 3 | they save a change sending If-Match 2 | the API returns 412 VERSION_CONFLICT with the current task and nothing is overwritten |
| AC-17 | FR-17 | a list with three tasks | the user drags the third task to the top | the new order persists on other devices after sync |
| AC-18 | FR-20 | a new account | registration completes | an Inbox list exists, and deleting it returns 409 DEFAULT_LIST |
| AC-19 | FR-21 | open tasks with due dates yesterday, today and tomorrow | the user opens the Today view | only the task due today in the user's time zone is shown and the Overdue view shows yesterday's |
| AC-20 | FR-22 | tasks with different tags and priorities | the user filters by one tag and high priority | only tasks matching both filters are returned, in the chosen sort order |
| AC-21 | FR-43 | a user who turned off assignment push notifications | a task is assigned to them | no push is sent, the in-app item still appears, and every email carries one-click unsubscribe |
| AC-22 | FR-60 | a signed-in user | they request a data export | a JSON and CSV archive is ready within 24 hours and delivered by a signed link that expires in 7 days |
| AC-23 | FR-62 | a new account created without ticking the marketing consent box | a marketing campaign runs | the user receives no marketing email and no consent record exists |

<!-- /speclang:acceptance -->

## 14. Error catalogue

Errors use RFC 9457 problem details. The client behavior column is what the UI must do.

<!-- speclang:errors -->

| Code | HTTP | Meaning | Client behavior | Used by |
|---|---|---|---|---|
| `INVALID_CREDENTIALS` | 401 | Email or password is wrong | Show a generic sign-in error | login |
| `REAUTH_REQUIRED` | 401 | A recent sign-in is required for this action | Ask the user to re-enter their password | enableMfa, deleteAccount |
| `TOKEN_REUSED` | 401 | A used refresh token was presented again; the token family is revoked | Sign in again | refreshToken |
| `UNAUTHENTICATED` | 401 | Missing or expired access token | Refresh once, then sign in again | all |
| `PLAN_LIMIT` | 402 | Plan limit reached; the body names the limit | Show an upgrade prompt | createList, createTask, requestAttachmentUpload, createReminder, inviteMember |
| `EMAIL_NOT_VERIFIED` | 403 | Sharing needs a verified email address | Prompt to verify email | inviteMember |
| `FORBIDDEN_ROLE` | 403 | Role lacks permission | Show the read-only state | updateList, deleteList, createTask, updateTask, completeTask, reopenTask, deleteTask, restoreTask, requestAttachmentUpload, createReminder, listMembers, inviteMember, setMemberRole, removeMember, createComment, deleteComment, pushChanges |
| `LIST_NOT_FOUND` | 404 | Missing or no access | Remove from the local store | updateList, deleteList, createTask, listMembers, inviteMember, setMemberRole, removeMember, listActivity |
| `SESSION_NOT_FOUND` | 404 | Session does not exist | Refresh the session list | revokeSession |
| `TASK_NOT_FOUND` | 404 | Missing or no access; the two are indistinguishable by design | Remove from the local store | getTask, updateTask, completeTask, reopenTask, deleteTask, restoreTask, requestAttachmentUpload, createReminder, createComment, deleteComment |
| `ALREADY_DONE` | 409 | Task already completed | Treat as success | completeTask |
| `ALREADY_MEMBER` | 409 | The user is already a member of the list | Show the existing member | inviteMember |
| `DEFAULT_LIST` | 409 | The default Inbox cannot be deleted or archived | Explain | updateList, deleteList |
| `EMAIL_EXISTS` | 409 | An account with this email exists | Offer sign in | register |
| `LAST_OWNER` | 409 | The last Owner cannot be removed or demoted | Explain and offer ownership transfer | setMemberRole, removeMember |
| `LIST_DELETED` | 409 | The parent list was deleted separately | Offer to restore the list first | restoreTask |
| `TAG_EXISTS` | 409 | A tag with this name exists; the body returns it | Use the existing tag | createTag |
| `CURSOR_EXPIRED` | 410 | Sync cursor is older than the compaction window | Run a full resync | pullChanges |
| `INVITE_EXPIRED` | 410 | Invitation link is expired or already used | Ask the owner to resend | acceptInvite |
| `RESET_LINK_INVALID` | 410 | Password reset link is expired or already used | Offer a new reset email | confirmPasswordReset |
| `VERSION_CONFLICT` | 412 | Stale If-Match version; the body includes the current state | Merge with the returned state, then retry | updateList, updateTask, completeTask, reopenTask, setMemberRole, pushChanges |
| `PAYLOAD_TOO_LARGE` | 413 | Body or file exceeds the limit | Show the size limit | requestAttachmentUpload, pushChanges |
| `UNSUPPORTED_TYPE` | 415 | File type is not allowed | Show the allowed types | requestAttachmentUpload |
| `INVALID_COMPLETED_AT` | 422 | completedAt is in the future | Use server time | completeTask |
| `VALIDATION_FAILED` | 422 | Field-level errors are listed in the errors array | Show inline field messages | register, login, oauthSignIn, verifyEmail, enableMfa, createList, updateList, listTasks, createTask, updateTask, createTag, createReminder, createComment, updateNotificationPreferences, pushChanges, updateConsent |
| `WEAK_PASSWORD` | 422 | Password is too short or found in the breach list | Explain the rule and ask for another | register, confirmPasswordReset |
| `ACCOUNT_LOCKED` | 423 | Too many failed sign-ins | Show the wait time | login, oauthSignIn |
| `RATE_LIMITED` | 429 | Too many requests | Back off for the Retry-After period | all |
| `INTERNAL` | 500 | Unexpected error | Retry with backoff and show the trace ID in the support dialog | all |
| `UNAVAILABLE` | 503 | Failover or maintenance | Retry with backoff | all |

<!-- /speclang:errors -->

## 29. Delivery plan

The phases below were reordered once the linter showed five requirements had no phase.

<!-- speclang:delivery -->

| Phase | Name | Scope | Delivers | Exit criteria |
|---|---|---|---|---|
| PH-0 | Foundations | Repo, CI/CD, IaC, auth and design system | - | Deployed empty app with login, tracing and alerts |
| PH-1 | MVP | Tasks, lists, tags, views, search and web app | FR-1, FR-2, FR-3, FR-5, FR-6, FR-10, FR-11, FR-12, FR-13, FR-17, FR-20, FR-21, FR-22, FR-23, FR-62 | Delivered requirements pass; NFRs met in load test |
| PH-2 | Everywhere | PWA offline sync, reminders and push, mobile apps | FR-40, FR-41, FR-42, FR-43, FR-50, FR-51, FR-52 | Delivered requirements pass; Sync chaos tests green |
| PH-3 | Teams | Sharing, roles, assignment, activity log and comments | FR-30, FR-31, FR-32, FR-33 | Authorization matrix tests complete |
| PH-4 | Hardening | Recurrence, attachments, MFA, export and deletion, pen test | FR-4, FR-14, FR-15, FR-16, FR-60, FR-61 | Compliance review signed off; DR drill passed |

## Rollout

| ID | Stage | Audience | Exit |
|---|---|---|---|
| RO-1 | Internal alpha | Team and friendly users | Daily builds stable; feature flags default off |
| RO-2 | Closed beta | About 500 invited users | Feedback channel running and daily error-budget review clean |
| RO-3 | Open beta | Waitlist in waves of 5%, 25% and 100% | Rollback by flag proven |
| RO-4 | General availability | Everyone | Status page, runbooks and on-call in place; pen test findings closed |

<!-- /speclang:delivery -->

### 29.1 Test coverage by phase

A requirement counts as covered when a test covers it directly or through one of its acceptance criteria.

<!-- speclang:coverage -->

| Phase | Name | Requirements | Covered | Not tested | No acceptance |
|---|---|---|---|---|---|
| PH-0 | Foundations | 0 | 0 | 0 | 0 |
| PH-1 | MVP | 15 | 14 | 0 | 1 |
| PH-2 | Everywhere | 7 | 6 | 0 | 1 |
| PH-3 | Teams | 4 | 2 | 0 | 2 |
| PH-4 | Hardening | 6 | 3 | 0 | 3 |

## PH-1 MVP

| Requirement | Pri | Acceptance | Tests | Status |
|---|---|---|---|---|
| FR-1 | M | AC-1, AC-2 | T-4, T-7 | covered |
| FR-2 | M | AC-13 | T-17 | covered |
| FR-3 | M | AC-14 | T-17 | covered |
| FR-5 | M | AC-3 | T-17 | covered |
| FR-6 | M | AC-15 | T-17 | covered |
| FR-10 | M | AC-4, AC-5 | T-2, T-4 | covered |
| FR-11 | M | AC-16 | T-2 | covered |
| FR-12 | M | AC-6 | T-4 | covered |
| FR-13 | M | AC-7 | T-4 | covered |
| FR-17 | M | AC-17 | T-4 | covered |
| FR-20 | M | AC-18 | T-2 | covered |
| FR-21 | M | AC-19 | T-2 | covered |
| FR-22 | M | AC-20 | T-2 | covered |
| FR-23 | S | - | - | no acceptance criterion |
| FR-62 | M | AC-23 | T-12 | covered |

## PH-2 Everywhere

| Requirement | Pri | Acceptance | Tests | Status |
|---|---|---|---|---|
| FR-40 | M | AC-9 | T-16 | covered |
| FR-41 | M | AC-9 | T-16 | covered |
| FR-42 | S | - | T-16 | no acceptance criterion |
| FR-43 | M | AC-21 | T-16 | covered |
| FR-50 | M | AC-10 | T-5 | covered |
| FR-51 | M | AC-11 | T-5 | covered |
| FR-52 | M | AC-10 | T-5 | covered |

## PH-3 Teams

| Requirement | Pri | Acceptance | Tests | Status |
|---|---|---|---|---|
| FR-30 | S | AC-8 | T-2 | covered |
| FR-31 | S | AC-8 | T-2 | covered |
| FR-32 | C | - | - | no acceptance criterion |
| FR-33 | S | - | - | no acceptance criterion |

## PH-4 Hardening

| Requirement | Pri | Acceptance | Tests | Status |
|---|---|---|---|---|
| FR-4 | S | - | - | no acceptance criterion |
| FR-14 | S | - | - | no acceptance criterion |
| FR-15 | S | AC-6 | T-4 | covered |
| FR-16 | S | - | - | no acceptance criterion |
| FR-60 | M | AC-22 | T-12 | covered |
| FR-61 | M | AC-12 | T-12 | covered |

Acceptance criteria with no test: none

<!-- /speclang:coverage -->

## 30. Risks

<!-- speclang:risks -->

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| RK-1 | Sync bugs cause data loss | Medium | High | Append-only change log, property tests, soft deletes and a client-side backup of the outbox | Engineering |
| RK-2 | Scope creep toward project management | High | Medium | Non-goals enforced in review and a roadmap gate | Product |
| RK-3 | Push notification reliability | Medium | Medium | Multi-channel fallback and delivery metrics | Engineering |
| RK-4 | Postgres becomes the search bottleneck | Low | Medium | Load tests in phase 1; a dedicated search engine is a planned option | Engineering |
| RK-5 | Single-region outage | Low | High | Cross-region backups and a documented warm-standby plan | Operations |

<!-- /speclang:risks -->

## 31. Traceability

<!-- speclang:traceability -->

| Requirement | Pri | Text | Acceptance | Operations | Components | Phase |
|---|---|---|---|---|---|---|
| FR-1 | M | The system shall allow registration with email and password; passwords must be at least 12 characters and checked against a breached-password list. | AC-1, AC-2 | register, login, getMe | OnboardingScreen | PH-1 |
| FR-2 | M | The system shall verify email addresses before enabling sharing features. | AC-13 | verifyEmail, inviteMember | OnboardingScreen | PH-1 |
| FR-3 | M | The system shall support OAuth 2.0 and OIDC sign-in with Google and Apple. | AC-14 | oauthSignIn | OnboardingScreen | PH-1 |
| FR-4 | S | The system shall support optional TOTP-based multi-factor authentication. | - | enableMfa | AccountSettingsScreen | PH-4 |
| FR-5 | M | The system shall support password reset via single-use links that expire in 30 minutes. | AC-3 | requestPasswordReset, confirmPasswordReset | AccountSettingsScreen | PH-1 |
| FR-6 | M | The system shall allow users to view and revoke active sessions. | AC-15 | refreshToken, logout, listSessions, revokeSession | AccountSettingsScreen | PH-1 |
| FR-10 | M | A user shall create a task with a title of 1 to 500 characters; optional fields are description, due date and time, priority, tags, list and parent task. | AC-4, AC-5 | getTask, createTask | InboxScreen, InlineAddTask | PH-1 |
| FR-11 | M | A user shall edit any field of a task they have write access to. | AC-16 | updateTask | InboxScreen, TaskDrawer | PH-1 |
| FR-12 | M | A user shall mark a task complete or reopen it; the completion time is recorded. | AC-6 | completeTask, reopenTask | InboxScreen, TaskRow | PH-1 |
| FR-13 | M | Deleting a task shall be a soft delete, recoverable for 30 days, then purged. | AC-7 | deleteTask, restoreTask | InboxScreen, TaskRow | PH-1 |
| FR-14 | S | Tasks shall support subtasks to one level of depth. | - | createTask | TaskDrawer | PH-4 |
| FR-15 | S | Tasks shall support recurrence rules; completing a recurring task generates the next instance. | AC-6 | completeTask | - | PH-4 |
| FR-16 | S | Tasks shall support checklists and file attachments of up to 10 MB each and 25 per task. | - | requestAttachmentUpload | TaskDrawer | PH-4 |
| FR-17 | M | Users shall reorder tasks manually within a list; the order persists across devices. | AC-17 | updateTask | InboxScreen, TaskRow | PH-1 |
| FR-20 | M | Users shall create, rename, archive and delete lists; every user has a default Inbox list that cannot be deleted. | AC-18 | register, listLists, createList, updateList, deleteList | InboxScreen, ListSettingsScreen | PH-1 |
| FR-21 | M | Built-in smart views shall include Today, Upcoming, Overdue, Completed and All. | AC-19 | listTasks | SmartViewScreen | PH-1 |
| FR-22 | M | Users shall filter by list, tag, priority, due range and status, and sort by due date, priority, created date or manual order. | AC-20 | listTasks, createTag | InboxScreen, TagPicker, FilterBar | PH-1 |
| FR-23 | S | The system shall provide full-text search across titles, descriptions and tags, returning results in under 300 ms at p95. | - | listTasks | SearchScreen | PH-1 |
| FR-30 | S | List owners shall invite members by email with one of three roles: Owner, Editor or Viewer. | AC-8 | listMembers, inviteMember, acceptInvite, setMemberRole, removeMember | ListSettingsScreen | PH-3 |
| FR-31 | S | Tasks in shared lists shall be assignable to a member; assignees are notified. | AC-8 | updateTask, listMembers, removeMember | AssigneeCombobox | PH-3 |
| FR-32 | C | Members shall comment on tasks; comments are immutable after 15 minutes except for deletion by the author or owner. | - | createComment, deleteComment | TaskDrawer | PH-3 |
| FR-33 | S | An activity log per shared list shall record who changed what and when. | - | listActivity | ListSettingsScreen, ActivityLog | PH-3 |
| FR-40 | M | Users shall set reminders at an absolute time or relative to the due date. | AC-9 | createReminder | - | PH-2 |
| FR-41 | M | Reminders shall be delivered by web push and mobile push, with email as the fallback. | AC-9 | createReminder | - | PH-2 |
| FR-42 | S | Users shall configure quiet hours and a daily digest. | - | updateNotificationPreferences | NotificationSettingsScreen | PH-2 |
| FR-43 | M | Every notification type shall be individually opt-out-able, and each email shall include one-click unsubscribe. | AC-21 | updateNotificationPreferences | NotificationSettingsScreen | PH-2 |
| FR-50 | M | Clients shall allow full create, edit and complete operations while offline, queuing changes locally. | AC-10 | pullChanges, pushChanges | SyncStatusIndicator | PH-2 |
| FR-51 | M | On reconnect, changes shall sync automatically; conflicts are resolved per field by server-assigned logical clock, with the losing change retained in history. | AC-11 | pullChanges, pushChanges | SyncStatusIndicator | PH-2 |
| FR-52 | M | Sync shall be idempotent: replaying the same change batch has no additional effect. | AC-10 | pushChanges | - | PH-2 |
| FR-60 | M | Users shall export all of their data as JSON and CSV. | AC-22 | exportData | AccountSettingsScreen | PH-4 |
| FR-61 | M | Users shall delete their account; all personal data is erased within 30 days and backups within 90 days. | AC-12 | deleteAccount | AccountSettingsScreen | PH-4 |
| FR-62 | M | The system shall record consent for marketing email separately from account creation. | AC-23 | register, updateConsent | AccountSettingsScreen | PH-1 |

## Non-functional requirements

| NFR | Category | Target | Verification | Tests and SLOs |
|---|---|---|---|---|
| NFR-1 | availability | 99.9% monthly (about 43 minutes of downtime) | SLO burn-rate alerts and resilience tests | SLO-1, T-9 |
| NFR-2 | latency | p50 < 80 ms, p95 < 200 ms, p99 < 500 ms | Load test at 2x expected peak | SLO-2, T-6 |
| NFR-3 | performance | LCP < 2.5 s, INP < 200 ms | Lighthouse CI budget | T-11 |
| NFR-4 | scalability | 500 req/s steady, 2000 req/s burst | Load test at 2x expected peak | T-6 |
| NFR-5 | durability | RPO <= 5 minutes, RTO <= 1 hour | Quarterly restore drill | T-10 |
| NFR-6 | security | OWASP ASVS Level 2; TLS 1.2+; AES-256 at rest | SAST, DAST and annual penetration test | T-7 |
| NFR-7 | privacy | GDPR and CCPA compliant; erasure within 30 days; regulator notice within 72 hours | Compliance audit and erasure tests | T-12 |
| NFR-8 | accessibility | WCAG 2.2 AA | Automated checks plus a screen reader pass | T-8 |
| NFR-9 | compatibility | Latest 2 versions of Chrome, Safari, Firefox, Edge; iOS 16+; Android 10+ | Browser and device test matrix | T-13 |
| NFR-10 | localization | UTF-8 everywhere; RTL layouts; 1 time zone per user | Pseudo-localization and RTL review | T-14 |
| NFR-11 | observability | 100% of requests carry a trace ID; every SLO has 1 alert | Dashboard and alert review | T-15 |
| NFR-12 | maintainability | 80% unit-test coverage on domain logic; 0 critical static-analysis findings | CI quality gates | T-1, T-3 |

<!-- /speclang:traceability -->
