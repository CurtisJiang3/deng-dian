document.documentElement.classList.add('js');

const readingBar = document.getElementById('readingBar');
const revealItems = [...document.querySelectorAll('.reveal')];
const intervalButtons = [...document.querySelectorAll('[data-interval]')];
const intervalInline = document.getElementById('intervalInline');
const stateButtons = [...document.querySelectorAll('[data-state]')];
const simulateButton = document.getElementById('simulateButton');
const notificationCard = document.getElementById('notificationCard');
const notifyTitle = document.getElementById('notifyTitle');
const notifyBody = document.getElementById('notifyBody');
const toast = document.getElementById('toast');
const toastText = document.getElementById('toastText');
const drawer = document.getElementById('detailDrawer');
const backdrop = document.getElementById('drawerBackdrop');
const closeDrawerButton = document.getElementById('closeDrawer');
const copyButtons = [...document.querySelectorAll('[data-copy-target]')];
let activeState = 'announce';
let toastTimer;
let lastFocusedElement;
let backdropTimer;

const examples = {
  announce: {
    title: 'Tibo 预告重置',
    body: '下周会有更多重置。现在可以开始安排用量了。',
    tag: '预告 · 尚未确认',
    heading: 'Tibo 有新的重置预告',
    chinese: '下周会有更多重置。当前是预告，具体生效时间仍需确认。',
    english: 'Sorry Gia. More resets coming next week.',
    original: 'https://x.com/thsottiaux/status/2103963215885701493'
  },
  confirm: {
    title: 'Tibo 确认重置生效',
    body: '重置已全部生效。现在去 Codex 查看你的实际额度。',
    tag: '确认 · 查看个人额度',
    heading: 'Tibo 确认重置已生效',
    chinese: '重置已全部生效。个人账号的具体额度，以 Codex 内显示为准。',
    english: 'Resets all propagated. That will be all. Have a fantastic weekend.',
    original: 'https://x.com/thsottiaux/status/2103911959544610829'
  }
};

function updateReadingProgress() {
  const available = document.documentElement.scrollHeight - window.innerHeight;
  readingBar.style.width = `${available > 0 ? (window.scrollY / available) * 100 : 0}%`;
}
window.addEventListener('scroll', updateReadingProgress, { passive: true });
window.addEventListener('resize', updateReadingProgress);
updateReadingProgress();

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.09, rootMargin: '0px 0px -35px 0px' });
  revealItems.forEach((item) => observer.observe(item));
} else {
  revealItems.forEach((item) => item.classList.add('is-visible'));
}

intervalButtons.forEach((button) => {
  button.setAttribute('aria-pressed', button.classList.contains('is-active') ? 'true' : 'false');
  button.addEventListener('click', () => {
    intervalButtons.forEach((item) => {
      const selected = item === button;
      item.classList.toggle('is-active', selected);
      item.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    intervalInline.textContent = button.dataset.interval;
  });
});

function updateExample(state) {
  activeState = state;
  stateButtons.forEach((button) => {
    const selected = button.dataset.state === state;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  const example = examples[state];
  notifyTitle.textContent = example.title;
  notifyBody.textContent = example.body;
  document.getElementById('drawerTag').textContent = example.tag;
  document.getElementById('drawerTitle').textContent = example.heading;
  document.getElementById('drawerChinese').textContent = example.chinese;
  document.getElementById('drawerEnglish').textContent = example.english;
  document.getElementById('drawerOriginal').href = example.original;
}
stateButtons.forEach((button) => button.addEventListener('click', () => updateExample(button.dataset.state)));

function showToast(message) {
  toastText.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 3200);
}

simulateButton.addEventListener('click', () => {
  notificationCard.classList.remove('is-pulsing');
  void notificationCard.offsetWidth;
  notificationCard.classList.add('is-pulsing');
  showToast(activeState === 'announce' ? 'Tibo 发出预告 · 飞书已提醒' : 'Tibo 确认生效 · 飞书已提醒');
  if (window.matchMedia('(max-width: 760px)').matches) {
    notificationCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
});

function openDrawer() {
  lastFocusedElement = document.activeElement;
  clearTimeout(backdropTimer);
  backdrop.hidden = false;
  document.body.classList.add('drawer-open');
  drawer.inert = false;
  drawer.setAttribute('aria-hidden', 'false');
  requestAnimationFrame(() => {
    backdrop.classList.add('is-visible');
    drawer.classList.add('is-visible');
    closeDrawerButton.focus();
  });
}

function closeDrawer() {
  drawer.classList.remove('is-visible');
  backdrop.classList.remove('is-visible');
  drawer.setAttribute('aria-hidden', 'true');
  drawer.inert = true;
  document.body.classList.remove('drawer-open');
  backdropTimer = setTimeout(() => { backdrop.hidden = true; }, 360);
  if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') lastFocusedElement.focus();
}

notificationCard.addEventListener('click', openDrawer);
closeDrawerButton.addEventListener('click', closeDrawer);
backdrop.addEventListener('click', closeDrawer);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && drawer.classList.contains('is-visible')) closeDrawer();
});

copyButtons.forEach((button) => {
  button.addEventListener('click', async () => {
    const command = document.getElementById(button.dataset.copyTarget).textContent.trim();
    let copied = false;
    try {
      await navigator.clipboard.writeText(command);
      copied = true;
    } catch {
      const helper = document.createElement('textarea');
      helper.value = command;
      helper.style.position = 'fixed';
      helper.style.opacity = '0';
      document.body.appendChild(helper);
      helper.select();
      copied = document.execCommand('copy');
      helper.remove();
    }
    button.textContent = copied ? '已复制' : '请手动复制';
    if (copied) showToast(`${button.dataset.copyLabel}已复制`);
    setTimeout(() => { button.textContent = '复制'; }, 2500);
  });
});

updateExample(activeState);
