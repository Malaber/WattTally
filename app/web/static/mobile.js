import { initFastPasskey } from "/api/v1/auth/assets/fastpasskey.js";
initFastPasskey();
document
  .querySelector("#mobile-approve")
  ?.addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      const response = await fetch("/api/mobile/approve", {
        method: "POST",
        headers: {
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')
            .content,
        },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Sign-in failed");
      location.href = result.callback;
    } catch (error) {
      document.querySelector("#mobile-error").textContent = error.message;
      event.target.disabled = false;
    }
  });
