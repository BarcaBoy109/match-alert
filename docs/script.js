const header = document.querySelector("[data-header]");
const navToggle = document.querySelector(".nav-toggle");
const navLinks = document.querySelector(".nav-links");
const commandText = document.querySelector("[data-command-text]");
const replyText = document.querySelector("[data-reply-text]");
const commandSteps = document.querySelectorAll(".step");
const copyButton = document.querySelector(".copy-command");

const setHeaderState = () => header?.classList.toggle("is-scrolled", window.scrollY > 18);
setHeaderState();
window.addEventListener("scroll", setHeaderState, { passive: true });

navToggle?.addEventListener("click", () => {
  const open = navToggle.getAttribute("aria-expanded") === "true";
  navToggle.setAttribute("aria-expanded", String(!open));
  navLinks?.classList.toggle("is-open", !open);
});

navLinks?.querySelectorAll("a").forEach((link) => {
  link.addEventListener("click", () => {
    navToggle?.setAttribute("aria-expanded", "false");
    navLinks.classList.remove("is-open");
  });
});

commandSteps.forEach((step) => {
  step.addEventListener("click", () => {
    commandSteps.forEach((item) => {
      item.classList.remove("is-active");
      item.setAttribute("aria-selected", "false");
    });
    step.classList.add("is-active");
    step.setAttribute("aria-selected", "true");
    commandText.textContent = step.dataset.command;
    replyText.textContent = step.dataset.reply;
  });
});

copyButton?.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(commandText.textContent);
    copyButton.setAttribute("aria-label", "Command copied");
    copyButton.style.color = "var(--lime)";
    window.setTimeout(() => {
      copyButton.setAttribute("aria-label", "Copy command");
      copyButton.style.color = "";
    }, 1500);
  } catch {
    // Clipboard access can be unavailable in local file previews.
  }
});

const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        revealObserver.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.12 }
);

document.querySelectorAll(".reveal").forEach((element) => revealObserver.observe(element));
document.querySelector("[data-year]").textContent = new Date().getFullYear();
