import { append, el } from '../dom.js';

export async function mount(root) {
  append(root, [el('h1', { class: 'h3 mb-3' }, 'Stock')]);
}
