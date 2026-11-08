import { OverlayScrollbarsComponent, OverlayScrollbarsComponentProps } from 'overlayscrollbars-react';
import './scroll-custom.module.scss';

export const enum CustomScrollVariant {
  dark = 'dark',
  light = 'light',
}

export const CustomScroll: FCClass<OverlayScrollbarsComponentProps & { variant?: CustomScrollVariant }> = ({
  children,
  className,
  variant = CustomScrollVariant.dark,
  ...props
}) => {
  return (
    <OverlayScrollbarsComponent
      defer
      {...props}
      className={className}
      options={{
        scrollbars: {
          theme: variant === CustomScrollVariant.dark ? 'os-theme-dark' : 'os-theme-light',
        },
      }}
    >
      {children}
    </OverlayScrollbarsComponent>
  );
};

export { centerCustomScroll } from './helpers';
