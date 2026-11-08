import cx from 'classix';
import Image from 'next/image';
import { MEDIA_SIZES } from '@/shared/enums';
import { TPicture } from './model';

export const Picture: FCClass<TPicture> = ({
  className,
  variant = '',
  src,
  tablet,
  desktop,
  alt,
  width,
  height,
  fill,
  noLazy,
  sizes,
  ...props
}) => {
  if (tablet ?? desktop) {
    return (
      <picture
        className={cx('block w-full h-full object-cover', className, variant ? '!object-contain' : '')}
      >
        {desktop && (
          <source
            srcSet={desktop}
            media={`(min-width: ${MEDIA_SIZES.DESKTOP}px)`}
          />
        )}
        {tablet && (
          <source
            srcSet={tablet}
            media={`(min-width: ${MEDIA_SIZES.TABLET}px)`}
          />
        )}
        <img src={src} loading={noLazy ? 'eager' : 'lazy'} alt={alt ?? 'image'} className={cx('block w-full h-full object-cover', variant ? '!object-contain' : '')} />
      </picture>
    );
  }

  return (
    <Image
      className={cx('block w-full h-full object-cover', className, variant ? '!object-contain' : '')}
      src={src}
      alt={alt ?? 'image'}
      width={width}
      height={height}
      fill={fill}
      sizes={sizes}
      quality={100}
      priority={noLazy}
      {...props}
    />
  );
};
