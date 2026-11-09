export default function Logo({ size = 30 }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path fill="#5BC8F5" d="M16 2 29 10v12L16 30 3 22V10z" />
      <path fill="#07090F" d="M16 7 24 15h-5v9h-6v-9H8z" />
    </svg>
  );
}
