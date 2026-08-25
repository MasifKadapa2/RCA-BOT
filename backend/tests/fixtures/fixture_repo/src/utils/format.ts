// Formats dates for the employee dashboard.
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toISOString().slice(0, 10);
}

export class DashboardHeader {
  constructor(private title: string) {}

  render(): string {
    return `<h1>${this.title}</h1>`;
  }
}
