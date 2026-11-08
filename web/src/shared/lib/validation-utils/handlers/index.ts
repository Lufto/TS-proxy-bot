import { schemaHandlers } from './_core';
import { handleObject } from './handle-object';
import { handleOptional } from './handle-optional';
import { handleArray } from './handle-array';
import { handleIntersection } from './handle-intersection';
import { handleDiscriminatedUnion } from './handle-discriminated-union';
import { handleUnion } from './handle-union';

schemaHandlers.set('object', handleObject);
schemaHandlers.set('optional', handleOptional);
schemaHandlers.set('array', handleArray);
schemaHandlers.set('intersect', handleIntersection);
schemaHandlers.set('variant', handleDiscriminatedUnion);
schemaHandlers.set('union', handleUnion);

export { FILTER_OUT, filterDataBySchema } from './_core';
