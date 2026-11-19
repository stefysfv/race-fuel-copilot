/** IndexedDB cache for the active race plan.

One record, key "active", so a refresh or a dead radio still has the plan,
the GPX text, and the race clock.
*/

const DB_NAME = "race-fuel-copilot";
const STORE = "session";
const COURSE_STORE = "courses";
const KEY = "active";
const RECENT_KEY = "recent";
const DB_VERSION = 2;

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
      if (!db.objectStoreNames.contains(COURSE_STORE)) db.createObjectStore(COURSE_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function saveSession(session) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(session, KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function loadSession() {
  const db = await openDb();
  const session = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const request = tx.objectStore(STORE).get(KEY);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return session;
}

/** Last five custom courses. Returns an empty list when IndexedDB is missing. */
export async function loadRecentCourses() {
  try {
    const db = await openDb();
    const courses = await new Promise((resolve, reject) => {
      const request = db.transaction(COURSE_STORE, "readonly").objectStore(COURSE_STORE).get(RECENT_KEY);
      request.onsuccess = () => resolve(Array.isArray(request.result) ? request.result : []);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return courses.slice(0, 5);
  } catch (error) {
    return [];
  }
}

/** Remember one uploaded GPX. The newest five are kept. */
export async function rememberCourse(course) {
  try {
    const current = await loadRecentCourses();
    const next = [course, ...current.filter((item) => item && item.id !== course.id)].slice(0, 5);
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(COURSE_STORE, "readwrite");
      tx.objectStore(COURSE_STORE).put(next, RECENT_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
    return next;
  } catch (error) {
    return null;
  }
}
