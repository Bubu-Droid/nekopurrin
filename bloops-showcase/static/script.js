// load the blog-wide post script from the root, like the @import in style.css
const shared = document.createElement("script");
shared.src = new URL("../../post.js", document.currentScript.src).href;
document.head.append(shared);
