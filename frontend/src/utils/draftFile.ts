// localStorage can only hold strings, so a draft's attached file is
// round-tripped through a base64 data URL rather than the File object itself.
export type DraftFile = { name: string; type: string; size: number; dataUrl: string };

export const fileToDataUrl = (file: File): Promise<string> => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result as string);
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(file);
});

export const dataUrlToFile = async (draft: DraftFile): Promise<File> => {
  const blob = await (await fetch(draft.dataUrl)).blob();
  return new File([blob], draft.name, { type: draft.type });
};
