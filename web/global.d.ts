type FCClass<P = object> = React.FC<P & React.PropsWithChildren & {
  className?: string;
}>;

type GetElementTypeFromArray<T> = T extends (infer U)[] ? U : never;

declare module '*.css' {
  const content: Record<string, string>;
  export default content;
}
declare module '*.scss' {
  const content: Record<string, string>;
  export default content;
}
