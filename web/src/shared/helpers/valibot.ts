import * as v from 'valibot';

/** Функция создаёт схему из массива допустимых строковых или числовых значений.
 *
 * пример
 *
 * ```ts
 * const totemIcons = ['panda', 'monkey', 'polarBear', 'koala', 'lion', 'dog', 'bear'] as const;
 * const totems = unionOfLiterals(totemIcons);
 * ```
 */
export function unionOfLiterals<T extends string | number>(constants: readonly T[]) {
  return v.picklist(constants as [T, ...T[]]);
}
